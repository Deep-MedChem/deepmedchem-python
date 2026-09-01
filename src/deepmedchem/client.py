from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx

from . import __version__
from .config import (
    DEFAULT_API_URL,
    CredentialSource,
    load_config,
    resolve_api_key,
    resolve_profile,
)
from .models import (
    Page,
    RunEvent,
    RunItem,
    RunResource,
    SampleResult,
    SearchResult,
    SelectionEstimate,
    SelectionResult,
    SelectionValidation,
    SubstructureResult,
)
from .selection import Run, Selection


class DeepMedChemError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "client_error",
        status_code: int | None = None,
        request_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.request_id = request_id
        self.retryable = retryable


def _credentials(
    api_key: str | None,
    credential_provider: CredentialSource | None = None,
    *,
    profile: str = "default",
) -> str:
    value = resolve_api_key(api_key, credential_provider, profile=profile)
    if not value:
        raise DeepMedChemError(
            f"No DeepMedChem credential is configured for profile {profile!r}. "
            f"Run `deepmedchem login --profile {profile}` or set DEEPMEDCHEM_API_KEY.",
            code="missing_api_key",
        )
    return value


def _raise_api_error(response: httpx.Response) -> None:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get("error") or payload.get("detail") or {}
    if not isinstance(error, dict):
        error = {"message": str(error)}
    raise DeepMedChemError(
        str(error.get("message") or f"API returned {response.status_code}"),
        code=str(error.get("code") or "api_error"),
        status_code=response.status_code,
        request_id=error.get("request_id") or response.headers.get("x-request-id"),
        retryable=bool(error.get("retryable", response.status_code in {429, 503, 504})),
    )


def _retry_delay(response: httpx.Response | None, attempt: int, backoff: float) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(30.0, max(0.0, float(retry_after)))
            except ValueError:
                pass
    return min(5.0, backoff * (2**attempt))


class _SyncSelections:
    def __init__(self, client: Client):
        self._client = client

    def validate(self, selection: Selection | dict[str, Any]) -> SelectionValidation:
        payload = Selection.model_validate(selection).to_dict()
        return SelectionValidation.model_validate(
            self._client._request("POST", "/api/v2/selections:validate", json=payload)
        )

    def estimate(self, selection: Selection | dict[str, Any]) -> SelectionEstimate:
        payload = Selection.model_validate(selection).to_dict()
        return SelectionEstimate.model_validate(
            self._client._request("POST", "/api/v2/selections:estimate", json=payload)
        )

    def create(self, selection: Selection | dict[str, Any]) -> SelectionResult:
        payload = Selection.model_validate(selection).to_dict()
        return SelectionResult.model_validate(
            self._client._request("POST", "/api/v2/selections", json=payload)
        )


class _SyncRuns:
    def __init__(self, client: Client):
        self._client = client

    def estimate(self, run: Run | dict[str, Any]) -> dict[str, Any]:
        return self._client._request(
            "POST", "/api/v2/runs:estimate", json=Run.model_validate(run).to_dict()
        )

    def create(self, run: Run | dict[str, Any], *, idempotency_key: str) -> RunResource:
        if not idempotency_key:
            raise ValueError("idempotency_key is required by the supported SDK")
        return RunResource.model_validate(
            self._client._request(
                "POST",
                "/api/v2/runs",
                json=Run.model_validate(run).to_dict(),
                headers={"Idempotency-Key": idempotency_key},
            )
        )

    def retrieve(self, run_id: str) -> RunResource:
        return RunResource.model_validate(self._client._request("GET", f"/api/v2/runs/{run_id}"))

    def iter_items(self, run_id: str, *, status: str | None = None) -> Iterator[RunItem]:
        cursor = None
        while True:
            params = {"cursor": cursor, "status_filter": status}
            page = Page.model_validate(
                self._client._request("GET", f"/api/v2/runs/{run_id}/items", params=params)
            )
            yield from (RunItem.model_validate(item) for item in page.data)
            if not page.next_cursor:
                return
            cursor = page.next_cursor

    def iter_results(self, run_id: str, *, order: str = "completion") -> Iterator[RunItem]:
        cursor = None
        while True:
            page = Page.model_validate(
                self._client._request(
                    "GET",
                    f"/api/v2/runs/{run_id}/results",
                    params={"cursor": cursor, "order": order},
                )
            )
            yield from (RunItem.model_validate(item) for item in page.data)
            if not page.next_cursor:
                return
            cursor = page.next_cursor

    def events(self, run_id: str, *, after: int = 0) -> list[RunEvent]:
        page = Page.model_validate(
            self._client._request("GET", f"/api/v2/runs/{run_id}/events", params={"after": after})
        )
        return [RunEvent.model_validate(item) for item in page.data]

    def watch(
        self,
        run_id: str,
        *,
        after: int = 0,
        poll_interval: float = 0.5,
    ) -> Iterator[RunEvent]:
        sequence = after
        while True:
            events = self.events(run_id, after=sequence)
            for event in events:
                sequence = max(sequence, event.sequence)
                yield event
            run = self.retrieve(run_id)
            if run.terminal and sequence >= run.last_event_sequence:
                return
            time.sleep(poll_interval)

    def wait(
        self, run_id: str, *, timeout: float | None = None, poll_interval: float = 0.5
    ) -> RunResource:
        started = time.monotonic()
        while True:
            run = self.retrieve(run_id)
            if run.terminal:
                return run
            if timeout is not None and time.monotonic() - started >= timeout:
                raise DeepMedChemError("Timed out waiting for run.", code="client_timeout")
            time.sleep(poll_interval)

    def cancel(self, run_id: str) -> RunResource:
        return RunResource.model_validate(
            self._client._request("POST", f"/api/v2/runs/{run_id}:cancel")
        )


class Client:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_url: str | None = None,
        profile: str | None = None,
        timeout: float = 45.0,
        transport=None,
        credential_provider: CredentialSource | None = None,
        application: str = "deepmedchem-python",
        application_version: str | None = None,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_backoff < 0:
            raise ValueError("retry_backoff must be non-negative")
        config = load_config()
        selected_profile = resolve_profile(profile, config)
        configured_url = api_url or config.profile(selected_profile).api_url or DEFAULT_API_URL
        token = _credentials(api_key, credential_provider, profile=selected_profile)
        client_version = application_version or __version__
        self._client = httpx.Client(
            base_url=configured_url.rstrip("/"),
            headers={
                "x-api-key": token,
                "x-dmc-client": application,
                "x-dmc-client-version": client_version,
                "x-dmc-sdk-version": __version__,
                "user-agent": f"{application}/{client_version} deepmedchem/{__version__}",
            },
            timeout=timeout,
            transport=transport,
        )
        self.selections = _SyncSelections(self)
        self.runs = _SyncRuns(self)
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            response = None
            try:
                response = self._client.request(method, path, **kwargs)
                if response.status_code in {429, 503, 504} and attempt < self._max_retries:
                    time.sleep(_retry_delay(response, attempt, self._retry_backoff))
                    continue
                if response.is_error:
                    _raise_api_error(response)
                return response.json()
            except DeepMedChemError:
                raise
            except httpx.HTTPError as error:
                if attempt < self._max_retries:
                    time.sleep(_retry_delay(response, attempt, self._retry_backoff))
                    continue
                raise DeepMedChemError(
                    f"Platform request failed: {type(error).__name__}",
                    code="transport_error",
                    retryable=True,
                ) from error
            except ValueError as error:
                raise DeepMedChemError(
                    "Platform returned invalid JSON.", code="invalid_response"
                ) from error
        raise AssertionError("unreachable")

    def catalog(self) -> dict[str, Any]:
        return self._request("GET", "/api/v2/catalog")

    def search(
        self,
        smiles: str,
        *,
        database: str,
        method: str = "morgan",
        limit: int = 20,
        shortlist_multiplier: int = 10,
        include_synthons: bool = False,
    ) -> SearchResult:
        if method not in {"morgan", "shape", "esp"}:
            raise ValueError("method must be one of: 'morgan', 'shape', 'esp'")
        if method != "morgan":
            return self.search_cheese(
                smiles,
                database=database,
                scorer=method,
                limit=limit,
                shortlist_multiplier=shortlist_multiplier,
                include_synthons=include_synthons,
            )
        return SearchResult.model_validate(
            self._request(
                "POST",
                "/api/v2/search",
                json={
                    "query_smiles": smiles,
                    "database_id": database,
                    "limit": limit,
                    "shortlist_multiplier": shortlist_multiplier,
                    "include_synthons": include_synthons,
                },
            )
        )

    def search_cheese(
        self,
        smiles: str,
        *,
        database: str,
        scorer: str,
        limit: int = 20,
        shortlist_multiplier: int = 10,
        include_synthons: bool = False,
    ) -> SearchResult:
        return SearchResult.model_validate(
            self._request(
                "POST",
                "/api/v2/search_cheese",
                json={
                    "query_smiles": smiles,
                    "database_id": database,
                    "scorer": scorer,
                    "limit": limit,
                    "shortlist_multiplier": shortlist_multiplier,
                    "include_synthons": include_synthons,
                },
            )
        )

    def search_substructure(
        self,
        query: str,
        *,
        query_format: str = "smarts",
        database: str,
        limit: int = 100,
        timeout_seconds: int = 30,
        include_synthons: bool = False,
    ) -> SubstructureResult:
        return SubstructureResult.model_validate(
            self._request(
                "POST",
                "/api/v2/search_substructure",
                json={
                    "query": {"format": query_format, "value": query},
                    "database_id": database,
                    "limit": limit,
                    "timeout_seconds": timeout_seconds,
                    "include_synthons": include_synthons,
                },
            )
        )

    def sample(
        self,
        *,
        database: str,
        count: int = 100,
        seed: int | None = None,
        include_synthons: bool = False,
    ) -> SampleResult:
        payload = {
            "database_id": database,
            "count": count,
            "include_synthons": include_synthons,
        }
        if seed is not None:
            payload["seed"] = seed
        return SampleResult.model_validate(self._request("POST", "/api/v2/sample", json=payload))


class _AsyncSelections:
    def __init__(self, client: AsyncClient):
        self._client = client

    async def validate(self, selection) -> SelectionValidation:
        return SelectionValidation.model_validate(
            await self._client._request(
                "POST",
                "/api/v2/selections:validate",
                json=Selection.model_validate(selection).to_dict(),
            )
        )

    async def estimate(self, selection) -> SelectionEstimate:
        return SelectionEstimate.model_validate(
            await self._client._request(
                "POST",
                "/api/v2/selections:estimate",
                json=Selection.model_validate(selection).to_dict(),
            )
        )

    async def create(self, selection) -> SelectionResult:
        return SelectionResult.model_validate(
            await self._client._request(
                "POST",
                "/api/v2/selections",
                json=Selection.model_validate(selection).to_dict(),
            )
        )


class _AsyncRuns:
    def __init__(self, client: AsyncClient):
        self._client = client

    async def estimate(self, run) -> dict[str, Any]:
        return await self._client._request(
            "POST", "/api/v2/runs:estimate", json=Run.model_validate(run).to_dict()
        )

    async def create(self, run, *, idempotency_key: str) -> RunResource:
        if not idempotency_key:
            raise ValueError("idempotency_key is required by the supported SDK")
        return RunResource.model_validate(
            await self._client._request(
                "POST",
                "/api/v2/runs",
                json=Run.model_validate(run).to_dict(),
                headers={"Idempotency-Key": idempotency_key},
            )
        )

    async def retrieve(self, run_id: str) -> RunResource:
        return RunResource.model_validate(
            await self._client._request("GET", f"/api/v2/runs/{run_id}")
        )

    async def iter_items(self, run_id: str) -> AsyncIterator[RunItem]:
        cursor = None
        while True:
            page = Page.model_validate(
                await self._client._request(
                    "GET", f"/api/v2/runs/{run_id}/items", params={"cursor": cursor}
                )
            )
            for item in page.data:
                yield RunItem.model_validate(item)
            if not page.next_cursor:
                return
            cursor = page.next_cursor

    async def iter_results(
        self, run_id: str, *, order: str = "completion"
    ) -> AsyncIterator[RunItem]:
        cursor = None
        while True:
            page = Page.model_validate(
                await self._client._request(
                    "GET",
                    f"/api/v2/runs/{run_id}/results",
                    params={"cursor": cursor, "order": order},
                )
            )
            for item in page.data:
                yield RunItem.model_validate(item)
            if not page.next_cursor:
                return
            cursor = page.next_cursor

    async def events(self, run_id: str, *, after: int = 0) -> list[RunEvent]:
        page = Page.model_validate(
            await self._client._request(
                "GET", f"/api/v2/runs/{run_id}/events", params={"after": after}
            )
        )
        return [RunEvent.model_validate(item) for item in page.data]

    async def watch(
        self, run_id: str, *, after: int = 0, poll_interval: float = 0.5
    ) -> AsyncIterator[RunEvent]:
        sequence = after
        while True:
            for event in await self.events(run_id, after=sequence):
                sequence = max(sequence, event.sequence)
                yield event
            run = await self.retrieve(run_id)
            if run.terminal and sequence >= run.last_event_sequence:
                return
            await asyncio.sleep(poll_interval)

    async def wait(
        self, run_id: str, *, timeout: float | None = None, poll_interval: float = 0.5
    ) -> RunResource:
        started = time.monotonic()
        while True:
            run = await self.retrieve(run_id)
            if run.terminal:
                return run
            if timeout is not None and time.monotonic() - started >= timeout:
                raise DeepMedChemError("Timed out waiting for run.", code="client_timeout")
            await asyncio.sleep(poll_interval)

    async def cancel(self, run_id: str) -> RunResource:
        return RunResource.model_validate(
            await self._client._request("POST", f"/api/v2/runs/{run_id}:cancel")
        )


class AsyncClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_url: str | None = None,
        profile: str | None = None,
        timeout: float = 45.0,
        transport=None,
        credential_provider: CredentialSource | None = None,
        application: str = "deepmedchem-python",
        application_version: str | None = None,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_backoff < 0:
            raise ValueError("retry_backoff must be non-negative")
        config = load_config()
        selected_profile = resolve_profile(profile, config)
        configured_url = api_url or config.profile(selected_profile).api_url or DEFAULT_API_URL
        token = _credentials(api_key, credential_provider, profile=selected_profile)
        client_version = application_version or __version__
        self._client = httpx.AsyncClient(
            base_url=configured_url.rstrip("/"),
            headers={
                "x-api-key": token,
                "x-dmc-client": application,
                "x-dmc-client-version": client_version,
                "x-dmc-sdk-version": __version__,
                "user-agent": f"{application}/{client_version} deepmedchem/{__version__}",
            },
            timeout=timeout,
            transport=transport,
        )
        self.selections = _AsyncSelections(self)
        self.runs = _AsyncRuns(self)
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, *_args) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            response = None
            try:
                response = await self._client.request(method, path, **kwargs)
                if response.status_code in {429, 503, 504} and attempt < self._max_retries:
                    await asyncio.sleep(_retry_delay(response, attempt, self._retry_backoff))
                    continue
                if response.is_error:
                    _raise_api_error(response)
                return response.json()
            except DeepMedChemError:
                raise
            except httpx.HTTPError as error:
                if attempt < self._max_retries:
                    await asyncio.sleep(_retry_delay(response, attempt, self._retry_backoff))
                    continue
                raise DeepMedChemError(
                    f"Platform request failed: {type(error).__name__}",
                    code="transport_error",
                    retryable=True,
                ) from error
            except ValueError as error:
                raise DeepMedChemError(
                    "Platform returned invalid JSON.", code="invalid_response"
                ) from error
        raise AssertionError("unreachable")

    async def catalog(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v2/catalog")

    async def search(self, smiles: str, **kwargs) -> SearchResult:
        method = kwargs.pop("method", "morgan")
        if method not in {"morgan", "shape", "esp"}:
            raise ValueError("method must be one of: 'morgan', 'shape', 'esp'")
        if method != "morgan":
            return await self.search_cheese(smiles, scorer=method, **kwargs)
        payload = {
            "query_smiles": smiles,
            "database_id": kwargs.pop("database"),
            "limit": kwargs.pop("limit", 20),
            "shortlist_multiplier": kwargs.pop("shortlist_multiplier", 10),
            "include_synthons": kwargs.pop("include_synthons", False),
        }
        if kwargs:
            raise TypeError(f"unexpected search arguments: {sorted(kwargs)}")
        return SearchResult.model_validate(
            await self._request("POST", "/api/v2/search", json=payload)
        )

    async def search_cheese(self, smiles: str, **kwargs) -> SearchResult:
        payload = {
            "query_smiles": smiles,
            "database_id": kwargs.pop("database"),
            "scorer": kwargs.pop("scorer"),
            "limit": kwargs.pop("limit", 20),
            "shortlist_multiplier": kwargs.pop("shortlist_multiplier", 10),
            "include_synthons": kwargs.pop("include_synthons", False),
        }
        if kwargs:
            raise TypeError(f"unexpected search arguments: {sorted(kwargs)}")
        return SearchResult.model_validate(
            await self._request("POST", "/api/v2/search_cheese", json=payload)
        )

    async def search_substructure(self, query: str, **kwargs) -> SubstructureResult:
        payload = {
            "query": {"format": kwargs.pop("query_format", "smarts"), "value": query},
            "database_id": kwargs.pop("database"),
            "limit": kwargs.pop("limit", 100),
            "timeout_seconds": kwargs.pop("timeout_seconds", 30),
            "include_synthons": kwargs.pop("include_synthons", False),
        }
        if kwargs:
            raise TypeError(f"unexpected substructure arguments: {sorted(kwargs)}")
        return SubstructureResult.model_validate(
            await self._request("POST", "/api/v2/search_substructure", json=payload)
        )

    async def sample(self, **kwargs) -> SampleResult:
        payload = {
            "database_id": kwargs.pop("database"),
            "count": kwargs.pop("count", 100),
            "include_synthons": kwargs.pop("include_synthons", False),
        }
        seed = kwargs.pop("seed", None)
        if seed is not None:
            payload["seed"] = seed
        if kwargs:
            raise TypeError(f"unexpected sample arguments: {sorted(kwargs)}")
        return SampleResult.model_validate(
            await self._request("POST", "/api/v2/sample", json=payload)
        )


# Explicit compatibility aliases make migration from the pre-split Navigator SDK mechanical.
DMCClient = Client
AsyncDMCClient = AsyncClient
DMCError = DeepMedChemError
