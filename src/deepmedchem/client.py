from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx

from . import __version__
from .auto import auto_options, require_auto, routing_database, similarity_plan
from .config import (
    DEFAULT_API_URL,
    CredentialSource,
    load_config,
    resolve_api_key,
    resolve_profile,
)
from .databases import resolve_database, retired_database_message
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
    Usage,
)
from .query_schema import QueryNode, StructureQuery
from .selection import Run, Selection
from .subsets import sample_selection, search_filters, similarity_selection

USAGE_PATH = "/api/v2/usage"


class DeepMedChemError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "client_error",
        status_code: int | None = None,
        request_id: str | None = None,
        retryable: bool = False,
        field: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.request_id = request_id
        self.retryable = retryable
        self.field = field
        self.details = details


class RetiredDatabaseError(DeepMedChemError, ValueError):
    """The platform API does not serve a database that classic CHEESE Search used to."""


def _sent_database_ids(value: Any) -> Iterator[str]:
    """Yield every ``database_id`` in a request body (search, selection or run template)."""

    if isinstance(value, dict):
        for key, item in value.items():
            if key == "database_id" and isinstance(item, str):
                yield item
            else:
                yield from _sent_database_ids(item)
    elif isinstance(value, list):
        for item in value:
            yield from _sent_database_ids(item)


def _retired_database_message(response: httpx.Response, code: str, field: Any) -> str | None:
    """Explain a "not served" answer about a retired classic CHEESE catalogue, else ``None``.

    "Not served" is ``unavailable_library``, or ``capability_unavailable`` on the database
    field (selections and runs, until they move to ``unavailable_library``). An operation
    that a served database does not support names another field and is never rewritten.
    """

    if code != "unavailable_library" and not (
        code == "capability_unavailable" and field == "database.database_id"
    ):
        return None
    try:
        sent = json.loads(response.request.content or b"null")
    except (RuntimeError, ValueError):
        return None
    for database_id in _sent_database_ids(sent):
        message = retired_database_message(database_id)
        if message:
            return message
    return None


def _credentials(
    api_key: str | None,
    credential_provider: CredentialSource | None = None,
    *,
    profile: str = "default",
    guest: bool = False,
) -> str | None:
    if guest:
        if api_key is not None or credential_provider is not None:
            raise ValueError("guest=True cannot be combined with api_key or credential_provider")
        return None
    return resolve_api_key(api_key, credential_provider, profile=profile) or None


_GUEST_ROUTES = {
    ("GET", "/api/v2/catalog"),
    ("POST", "/api/v2/search"),
    ("POST", "/api/v2/search_cheese"),
    ("POST", "/api/v2/sample"),
}


def _check_guest_access(is_guest: bool, method: str, path: str) -> None:
    if is_guest and (method, path) not in _GUEST_ROUTES:
        raise DeepMedChemError(
            "This operation requires an API key. Guest access supports catalog, "
            "unfiltered single-database similarity search and sampling. "
            "Run `dmc login` or set DEEPMEDCHEM_API_KEY, then create a new client "
            "without guest=True.",
            code="missing_api_key",
        )


def _should_retry(response: httpx.Response) -> bool:
    if response.status_code not in {429, 503, 504}:
        return False
    # An exhausted hourly/daily guest budget cannot recover during our short retries.
    if response.status_code == 429:
        try:
            error = response.json().get("error", {})
            if isinstance(error, dict) and error.get("code") == "guest_limit":
                return False
        except (ValueError, AttributeError):
            pass
    return True


def _raise_api_error(response: httpx.Response) -> None:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get("error") or payload.get("detail") or {}
    if not isinstance(error, dict):
        error = {"message": str(error)}
    code = str(error.get("code") or "api_error")
    message = str(error.get("message") or f"API returned {response.status_code}")
    error_class = DeepMedChemError
    retired = _retired_database_message(response, code, error.get("field"))
    if retired:
        error_class, code, message = RetiredDatabaseError, "retired_database", retired
    raise error_class(
        message,
        code=code,
        status_code=response.status_code,
        request_id=error.get("request_id") or response.headers.get("x-request-id"),
        retryable=bool(error.get("retryable", response.status_code in {429, 503, 504})),
        field=error.get("field"),
        details=error.get("details"),
    )


def _usage_unavailable(error: DeepMedChemError) -> DeepMedChemError:
    """Explain a 404 from a platform deployment that predates GET /api/v2/usage."""

    if error.status_code != 404:
        return error
    return DeepMedChemError(
        "This DeepMedChem API server does not provide account usage (GET "
        f"{USAGE_PATH} returned 404). It predates the usage endpoint; the CHEESE web app "
        "shows the daily credit balance.",
        code="usage_unavailable",
        status_code=404,
        request_id=error.request_id,
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
    """Synchronous API client. With no key, use the server's limited guest tier.

    Set ``guest=True`` to skip saved/environment credentials. Account-only operations
    require a configured key. ``is_guest`` reports the resolved authentication mode.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_url: str | None = None,
        guest: bool = False,
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
        selected = config.profile(selected_profile)
        configured_url = api_url or selected.api_url or DEFAULT_API_URL
        token = _credentials(api_key, credential_provider, profile=selected_profile, guest=guest)
        self.is_guest = token is None
        client_version = application_version or __version__
        self._client = httpx.Client(
            base_url=configured_url.rstrip("/"),
            headers={
                **({"x-api-key": token} if token is not None else {}),
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
        # Imported here because the optimization module builds on this one.
        from .optimization import Optimizations

        self.optimizations = Optimizations(self)
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        return self._request_with_response(method, path, **kwargs)[0]

    def _request_with_response(
        self, method: str, path: str, **kwargs
    ) -> tuple[Any, httpx.Response]:
        """Send one request with retries; return the decoded JSON and the final response."""

        _check_guest_access(self.is_guest, method, path)
        for attempt in range(self._max_retries + 1):
            response = None
            try:
                response = self._client.request(method, path, **kwargs)
                if _should_retry(response) and attempt < self._max_retries:
                    time.sleep(_retry_delay(response, attempt, self._retry_backoff))
                    continue
                if response.is_error:
                    _raise_api_error(response)
                return response.json(), response
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
        """Return the platform catalog of searchable databases."""

        return self._request("GET", "/api/v2/catalog")

    def usage(self) -> Usage:
        """Return the account plan and today's CHEESE Credit balance; costs no credits."""

        try:
            payload = self._request("GET", USAGE_PATH)
        except DeepMedChemError as error:
            raise _usage_unavailable(error) from error
        return Usage.model_validate(payload)

    def search(
        self,
        smiles: str,
        *,
        database: str = "auto",
        method: str = "morgan",
        limit: int = 20,
        include_synthons: bool = False,
        subset: str | list[str] | None = None,
        where=None,
        require=None,
        exclude=None,
        max_price: float | None = None,
        keep_unpriced: bool = False,
        synthesis: str | int | None = None,
        include_catalogues: bool = False,
        structure_query: StructureQuery | QueryNode | dict[str, Any] | None = None,
        availability: str | None = None,
        scope: str = "orderable",
        databases: list[str] | None = None,
        timeout_seconds: int = 30,
    ) -> SearchResult:
        """Rank molecules of ``database`` by similarity to ``smiles``.

        ``subset`` (a name from ``deepmedchem.subsets.SUBSETS``, or ``database=
        "synple:amino-acids"``), ``where`` (``"mw<=450"``), ``require`` and ``exclude``
        (SMARTS) restrict the search. ``max_price`` keeps products priced at most that
        many USD (1 mg, US delivery); unpriced products are dropped unless
        ``keep_unpriced``. Filtered searches run as one selection: the server enforces
        every constraint on every returned molecule and returns a
        :class:`SelectionResult`.
        """

        if method not in {"morgan", "shape", "esp"}:
            raise ValueError("method must be one of: 'morgan', 'shape', 'esp'")
        database, filters = search_filters(
            database,
            subset=subset,
            where=where,
            require=require,
            exclude=exclude,
            max_price=max_price,
            keep_unpriced=keep_unpriced,
            structure_query=structure_query,
        )
        database, databases, include_catalogues = routing_database(
            database,
            synthesis=synthesis,
            include_catalogues=include_catalogues,
            databases=databases,
        )
        require_auto(database, availability=availability, scope=scope, databases=databases)
        if database == "auto":
            selection = similarity_selection(
                smiles,
                database=database,
                filters=filters,
                method=method,
                limit=limit,
                include_synthons=include_synthons,
            )
            return SearchResult.model_validate(
                self._request(
                    "POST",
                    "/api/v2/search_auto",
                    json={
                        "selection": selection.to_dict(),
                        **auto_options(
                            synthesis=synthesis,
                            include_catalogues=include_catalogues,
                            availability=availability,
                            scope=scope,
                            databases=databases,
                            timeout_seconds=timeout_seconds,
                        ),
                    },
                )
            )
        if timeout_seconds != 30:
            raise ValueError("search timeout_seconds requires database='auto'")
        if filters:
            return self.selections.create(
                similarity_selection(
                    smiles,
                    database=database,
                    filters=filters,
                    method=method,
                    limit=limit,
                    include_synthons=include_synthons,
                )
            )
        database_id = resolve_database(database)
        if method != "morgan":
            return self.search_cheese(
                smiles,
                database=database,
                scorer=method,
                limit=limit,
                include_synthons=include_synthons,
            )
        return SearchResult.model_validate(
            self._request(
                "POST",
                "/api/v2/search",
                json={
                    "query_smiles": smiles,
                    "database_id": database_id,
                    "limit": limit,
                    "include_synthons": include_synthons,
                },
            )
        )

    def validate_query(self, query: StructureQuery | QueryNode | dict[str, Any]) -> dict[str, Any]:
        """Compile a native query on the service without searching or charging credits."""
        from .query_schema import as_structure_query

        return self._request(
            "POST",
            "/api/v2/structure_queries:validate",
            json=as_structure_query(query).model_dump(mode="json"),
        )

    def plan_search(self, smiles: str, **kwargs) -> dict[str, Any]:
        """Preview automatic source selection and credit cost without searching."""
        return self._request(
            "POST", "/api/v2/search_auto:plan", json=similarity_plan(smiles, **kwargs)
        )

    def search_many(
        self,
        queries,
        *,
        database: str,
        method: str = "morgan",
        limit: int = 20,
        state_file: str | os.PathLike[str] | None = None,
        timeout: float | None = None,
        poll_interval: float = 1.0,
        on_progress=None,
    ):
        """Search each query molecule separately in one durable run and wait for the results.

        ``queries`` is a list of SMILES, a ``{query_id: smiles}`` mapping, ``(id, smiles)``
        pairs, or the output of ``deepmedchem.batch.read_queries(path)``. Every record is
        its own query (duplicates are kept); ``limit`` is the number of hits *per query*.
        Each successful query costs one credit; failed queries are refunded.

        Every call starts a new run. Pass ``state_file`` to save the idempotency key
        (before the run is created) and the run ID, then reconnect after an
        interruption with :meth:`resume_search_many` instead of searching (and
        paying) again. Without ``state_file``, an error raised after the request was
        sent carries ``error.state``; pass that to :meth:`resume_search_many`.
        Short network outages while waiting are retried automatically.
        Never prompts, so it is safe in scripts.
        """

        from .batch import normalize_queries, start_batch, wait_and_collect

        database_id = resolve_database(database)
        query_list = normalize_queries(queries)
        state = start_batch(
            self,
            query_list,
            database=database_id,
            method=method,
            limit=limit,
            state_file=state_file,
        )
        return wait_and_collect(
            self,
            state,
            timeout=timeout,
            poll_interval=poll_interval,
            on_progress=on_progress,
            state_file=state_file,
        )

    def resume_search_many(
        self,
        state,
        *,
        timeout: float | None = None,
        poll_interval: float = 1.0,
        on_progress=None,
    ):
        """Reconnect to a batch run saved with ``state_file`` and return its results.

        ``state`` is the state file path, its loaded dictionary, or ``error.state`` from
        a failed :meth:`search_many`. If the run was never confirmed as created, the
        same request is sent again with the saved idempotency key, so the server
        returns the existing run (or creates it once). Nothing is charged twice.
        """

        from .batch import ensure_started, load_state, wait_and_collect

        state_file = None if isinstance(state, dict) else state
        loaded = state if isinstance(state, dict) else load_state(state)
        ensure_started(self, loaded, state_file)
        return wait_and_collect(
            self,
            loaded,
            timeout=timeout,
            poll_interval=poll_interval,
            on_progress=on_progress,
            state_file=state_file,
        )

    def search_cheese(
        self,
        smiles: str,
        *,
        database: str,
        scorer: str,
        limit: int = 20,
        include_synthons: bool = False,
    ) -> SearchResult:
        if database == "auto":
            return self.search(
                smiles,
                database=database,
                method=scorer,
                limit=limit,
                include_synthons=include_synthons,
            )
        database_id = resolve_database(database)
        return SearchResult.model_validate(
            self._request(
                "POST",
                "/api/v2/search_cheese",
                json={
                    "query_smiles": smiles,
                    "database_id": database_id,
                    "scorer": scorer,
                    "limit": limit,
                    "include_synthons": include_synthons,
                },
            )
        )

    def search_substructure(
        self,
        query: str | StructureQuery | QueryNode | dict[str, Any],
        *,
        query_format: str = "smarts",
        where=None,
        require=None,
        exclude=None,
        max_price=None,
        keep_unpriced=False,
        database: str,
        limit: int = 100,
        timeout_seconds: int = 30,
        include_synthons: bool = False,
        synthesis: str | int | None = None,
        include_catalogues: bool = False,
        availability: str | None = None,
        scope: str = "orderable",
        databases: list[str] | None = None,
    ) -> SubstructureResult:
        query_payload = _substructure_query(
            query,
            query_format,
            where=where,
            require=require,
            exclude=exclude,
            max_price=max_price,
            keep_unpriced=keep_unpriced,
        )
        database_id = resolve_database(database)
        database_id, databases, include_catalogues = routing_database(
            database_id,
            synthesis=synthesis,
            include_catalogues=include_catalogues,
            databases=databases,
        )
        require_auto(database_id, availability=availability, scope=scope, databases=databases)
        if database_id == "auto":
            return SubstructureResult.model_validate(
                self._request(
                    "POST",
                    "/api/v2/search_auto",
                    json={
                        "substructure": {
                            **query_payload,
                            "database_id": "auto",
                            "limit": limit,
                            "timeout_seconds": timeout_seconds,
                            "include_synthons": include_synthons,
                        },
                        **auto_options(
                            synthesis=synthesis,
                            include_catalogues=include_catalogues,
                            availability=availability,
                            scope=scope,
                            databases=databases,
                            timeout_seconds=timeout_seconds,
                        ),
                    },
                )
            )
        return SubstructureResult.model_validate(
            self._request(
                "POST",
                "/api/v2/search_substructure",
                json={
                    **query_payload,
                    "database_id": database_id,
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
        subset: str | list[str] | None = None,
        where=None,
        max_price: float | None = None,
        keep_unpriced: bool = False,
        structure_query: StructureQuery | QueryNode | dict[str, Any] | None = None,
    ) -> SampleResult | SelectionResult:
        """Draw random molecules, optionally restricted to a property subset or price."""

        database, filters = search_filters(
            database,
            subset=subset,
            where=where,
            max_price=max_price,
            keep_unpriced=keep_unpriced,
            structure_query=structure_query,
        )
        if filters:
            return self.selections.create(
                sample_selection(
                    database=database,
                    filters=filters,
                    count=count,
                    seed=seed,
                    include_synthons=include_synthons,
                )
            )
        database_id = resolve_database(database)
        payload = {
            "database_id": database_id,
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
    """Async counterpart of :class:`Client`, including automatic/explicit guest access."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_url: str | None = None,
        guest: bool = False,
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
        selected = config.profile(selected_profile)
        configured_url = api_url or selected.api_url or DEFAULT_API_URL
        token = _credentials(api_key, credential_provider, profile=selected_profile, guest=guest)
        self.is_guest = token is None
        client_version = application_version or __version__
        self._client = httpx.AsyncClient(
            base_url=configured_url.rstrip("/"),
            headers={
                **({"x-api-key": token} if token is not None else {}),
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
        from .optimization import AsyncOptimizations

        self.optimizations = AsyncOptimizations(self)
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, *_args) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        return (await self._request_with_response(method, path, **kwargs))[0]

    async def _request_with_response(
        self, method: str, path: str, **kwargs
    ) -> tuple[Any, httpx.Response]:
        """Send one request with retries; return the decoded JSON and the final response."""

        _check_guest_access(self.is_guest, method, path)
        for attempt in range(self._max_retries + 1):
            response = None
            try:
                response = await self._client.request(method, path, **kwargs)
                if _should_retry(response) and attempt < self._max_retries:
                    await asyncio.sleep(_retry_delay(response, attempt, self._retry_backoff))
                    continue
                if response.is_error:
                    _raise_api_error(response)
                return response.json(), response
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
        """Return the platform catalog of searchable databases."""

        return await self._request("GET", "/api/v2/catalog")

    async def usage(self) -> Usage:
        """Return the account plan and today's CHEESE Credit balance; costs no credits."""

        try:
            payload = await self._request("GET", USAGE_PATH)
        except DeepMedChemError as error:
            raise _usage_unavailable(error) from error
        return Usage.model_validate(payload)

    async def search(self, smiles: str, **kwargs) -> SearchResult:
        method = kwargs.pop("method", "morgan")
        if method not in {"morgan", "shape", "esp"}:
            raise ValueError("method must be one of: 'morgan', 'shape', 'esp'")
        database, filters = search_filters(
            kwargs.pop("database", "auto"),
            subset=kwargs.pop("subset", None),
            where=kwargs.pop("where", None),
            require=kwargs.pop("require", None),
            exclude=kwargs.pop("exclude", None),
            max_price=kwargs.pop("max_price", None),
            keep_unpriced=kwargs.pop("keep_unpriced", False),
            structure_query=kwargs.pop("structure_query", None),
        )
        synthesis = kwargs.pop("synthesis", None)
        include_catalogues = kwargs.pop("include_catalogues", False)
        availability = kwargs.pop("availability", None)
        scope = kwargs.pop("scope", "orderable")
        databases = kwargs.pop("databases", None)
        timeout_seconds = kwargs.pop("timeout_seconds", 30)
        database, databases, include_catalogues = routing_database(
            database,
            synthesis=synthesis,
            include_catalogues=include_catalogues,
            databases=databases,
        )
        require_auto(database, availability=availability, scope=scope, databases=databases)
        if database == "auto":
            selection = similarity_selection(
                smiles,
                database=database,
                filters=filters,
                method=method,
                limit=kwargs.pop("limit", 20),
                include_synthons=kwargs.pop("include_synthons", False),
            )
            if kwargs:
                raise TypeError(f"unexpected search arguments: {sorted(kwargs)}")
            return SearchResult.model_validate(
                await self._request(
                    "POST",
                    "/api/v2/search_auto",
                    json={
                        "selection": selection.to_dict(),
                        **auto_options(
                            synthesis=synthesis,
                            include_catalogues=include_catalogues,
                            availability=availability,
                            scope=scope,
                            databases=databases,
                            timeout_seconds=timeout_seconds,
                        ),
                    },
                )
            )
        if timeout_seconds != 30:
            raise ValueError("search timeout_seconds requires database='auto'")
        if filters:
            selection = similarity_selection(
                smiles,
                database=database,
                filters=filters,
                method=method,
                limit=kwargs.pop("limit", 20),
                include_synthons=kwargs.pop("include_synthons", False),
            )
            if kwargs:
                raise TypeError(f"unexpected search arguments: {sorted(kwargs)}")
            return await self.selections.create(selection)
        database_id = resolve_database(database)
        if method != "morgan":
            return await self.search_cheese(smiles, database=database_id, scorer=method, **kwargs)
        payload = {
            "query_smiles": smiles,
            "database_id": database_id,
            "limit": kwargs.pop("limit", 20),
            "include_synthons": kwargs.pop("include_synthons", False),
        }
        if kwargs:
            raise TypeError(f"unexpected search arguments: {sorted(kwargs)}")
        return SearchResult.model_validate(
            await self._request("POST", "/api/v2/search", json=payload)
        )

    async def validate_query(
        self, query: StructureQuery | QueryNode | dict[str, Any]
    ) -> dict[str, Any]:
        """Compile a native query on the service without searching or charging credits."""
        from .query_schema import as_structure_query

        return await self._request(
            "POST",
            "/api/v2/structure_queries:validate",
            json=as_structure_query(query).model_dump(mode="json"),
        )

    async def plan_search(self, smiles: str, **kwargs) -> dict[str, Any]:
        """Preview automatic source selection and credit cost without searching."""
        return await self._request(
            "POST", "/api/v2/search_auto:plan", json=similarity_plan(smiles, **kwargs)
        )

    async def search_cheese(self, smiles: str, **kwargs) -> SearchResult:
        if kwargs.get("database") == "auto":
            return await self.search(smiles, method=kwargs.pop("scorer"), **kwargs)
        database_id = resolve_database(kwargs.pop("database"))
        scorer = kwargs.pop("scorer")
        payload = {
            "query_smiles": smiles,
            "database_id": database_id,
            "scorer": scorer,
            "limit": kwargs.pop("limit", 20),
            "include_synthons": kwargs.pop("include_synthons", False),
        }
        if kwargs:
            raise TypeError(f"unexpected search arguments: {sorted(kwargs)}")
        return SearchResult.model_validate(
            await self._request("POST", "/api/v2/search_cheese", json=payload)
        )

    async def search_substructure(
        self, query: str | StructureQuery | QueryNode | dict[str, Any], **kwargs
    ) -> SubstructureResult:
        database_id = resolve_database(kwargs.pop("database"))
        synthesis = kwargs.pop("synthesis", None)
        include_catalogues = kwargs.pop("include_catalogues", False)
        availability = kwargs.pop("availability", None)
        scope = kwargs.pop("scope", "orderable")
        databases = kwargs.pop("databases", None)
        database_id, databases, include_catalogues = routing_database(
            database_id,
            synthesis=synthesis,
            include_catalogues=include_catalogues,
            databases=databases,
        )
        require_auto(database_id, availability=availability, scope=scope, databases=databases)
        payload = {
            **_substructure_query(
                query,
                kwargs.pop("query_format", "smarts"),
                **{
                    key: kwargs.pop(key, None)
                    for key in ("where", "require", "exclude", "max_price", "keep_unpriced")
                },
            ),
            "database_id": database_id,
            "limit": kwargs.pop("limit", 100),
            "timeout_seconds": kwargs.pop("timeout_seconds", 30),
            "include_synthons": kwargs.pop("include_synthons", False),
        }
        if kwargs:
            raise TypeError(f"unexpected substructure arguments: {sorted(kwargs)}")
        if database_id == "auto":
            return SubstructureResult.model_validate(
                await self._request(
                    "POST",
                    "/api/v2/search_auto",
                    json={
                        "substructure": payload,
                        **auto_options(
                            synthesis=synthesis,
                            include_catalogues=include_catalogues,
                            availability=availability,
                            scope=scope,
                            databases=databases,
                            timeout_seconds=payload["timeout_seconds"],
                        ),
                    },
                )
            )
        return SubstructureResult.model_validate(
            await self._request("POST", "/api/v2/search_substructure", json=payload)
        )

    async def sample(self, **kwargs) -> SampleResult | SelectionResult:
        database, filters = search_filters(
            kwargs.pop("database"),
            subset=kwargs.pop("subset", None),
            where=kwargs.pop("where", None),
            max_price=kwargs.pop("max_price", None),
            keep_unpriced=kwargs.pop("keep_unpriced", False),
            structure_query=kwargs.pop("structure_query", None),
        )
        if filters:
            selection = sample_selection(
                database=database,
                filters=filters,
                count=kwargs.pop("count", 100),
                seed=kwargs.pop("seed", None),
                include_synthons=kwargs.pop("include_synthons", False),
            )
            if kwargs:
                raise TypeError(f"unexpected sample arguments: {sorted(kwargs)}")
            return await self.selections.create(selection)
        database_id = resolve_database(database)
        payload = {
            "database_id": database_id,
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


def _substructure_query(
    query,
    query_format,
    *,
    where=None,
    require=None,
    exclude=None,
    max_price=None,
    keep_unpriced=False,
):
    from .chemistry import RequireStructure
    from .query_schema import Contains, Not, as_structure_query, combine_queries
    from .subsets import Filters, Pattern

    filters = Filters.build(
        where=where,
        require=require,
        exclude=exclude,
        max_price=max_price,
        keep_unpriced=keep_unpriced,
    )
    if isinstance(query, str):
        payload = {"query": {"format": query_format, "value": query}}
        native = None
    else:
        native = as_structure_query(query)
        payload = {}
    for excluded, patterns in ((False, filters.require), (True, filters.exclude)):
        for pattern in patterns:
            if isinstance(pattern, Pattern):
                node = Contains(
                    **{pattern.format: pattern.smarts},
                    min_count=pattern.min_count,
                    max_count=pattern.max_count,
                )
            elif isinstance(pattern, RequireStructure):
                node = Contains(**pattern.model_dump(exclude_none=True))
            else:
                raise TypeError("unsupported substructure condition")
            native = combine_queries(native, Not(condition=node) if excluded else node)
    if native is not None:
        payload["structure_query"] = native.model_dump(mode="json", exclude_none=True)
    names = {
        "rdkit.mol_wt": "MolWt",
        "rdkit.crippen_logp": "MolLogP",
        "rdkit.hbd": "HBD",
        "rdkit.hba": "HBA",
        "rdkit.rotatable_bonds": "RotB",
        "rdkit.tpsa": "TPSA",
        "rdkit.fraction_csp3": "FractionCSP3",
        "rdkit.aromatic_rings": "AromaticRings",
    }
    bounds = {}
    for condition in filters.where:
        name = names.get(condition.property_id)
        if name is None:
            raise ValueError("substructure property filters require a built-in RDKit descriptor")
        entry = bounds.setdefault(name, {})
        op, value = condition.operator, condition.value
        updates = (
            {"min": (value[0], True), "max": (value[1], True)}
            if op == "range"
            else {"min": (value, True), "max": (value, True)}
            if op == "eq"
            else {"min" if op in {"gt", "gte"} else "max": (value, op in {"gte", "lte"})}
        )
        for side, (number, inclusive) in updates.items():
            previous = entry.get(side)
            tighter = previous is None or (
                number > previous if side == "min" else number < previous
            )
            if tighter:
                entry[side], entry[side + "_inclusive"] = number, inclusive
            elif number == previous:
                entry[side + "_inclusive"] = entry[side + "_inclusive"] and inclusive
        low, high = entry.get("min"), entry.get("max")
        if (
            low is not None
            and high is not None
            and (
                low > high
                or (
                    low == high
                    and not (entry.get("min_inclusive", True) and entry.get("max_inclusive", True))
                )
            )
        ):
            raise ValueError("contradictory substructure property bounds")
    if bounds:
        payload["property_constraints"] = bounds
    if filters.max_price is not None:
        payload["price"] = {
            "max": filters.max_price,
            "missing": "keep" if filters.keep_unpriced else "reject",
        }
    return payload
