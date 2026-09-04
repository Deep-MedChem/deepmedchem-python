"""Short-lived, module-level operations for notebooks and one-off calls."""

from __future__ import annotations

from typing import Any

from .client import Client
from .config import CredentialSource
from .models import SampleResult, SearchResult, SubstructureResult, Usage


def _client_options(
    *,
    api_key: str | None,
    api_url: str | None,
    profile: str | None,
    credential_provider: CredentialSource | None,
    timeout: float,
    transport: Any,
) -> dict[str, Any]:
    return {
        "api_key": api_key,
        "api_url": api_url,
        "profile": profile,
        "credential_provider": credential_provider,
        "timeout": timeout,
        "transport": transport,
    }


def search(
    smiles: str,
    *,
    database: str,
    method: str = "morgan",
    limit: int = 20,
    include_synthons: bool = False,
    api_key: str | None = None,
    api_url: str | None = None,
    profile: str | None = None,
    credential_provider: CredentialSource | None = None,
    timeout: float = 45.0,
    transport: Any = None,
) -> SearchResult:
    """Run one ranked similarity search and close its internal transport."""

    with Client(
        **_client_options(
            api_key=api_key,
            api_url=api_url,
            profile=profile,
            credential_provider=credential_provider,
            timeout=timeout,
            transport=transport,
        )
    ) as client:
        return client.search(
            smiles,
            database=database,
            method=method,
            limit=limit,
            include_synthons=include_synthons,
        )


def substructure(
    query: str,
    *,
    database: str,
    format: str = "smarts",
    limit: int = 100,
    timeout_seconds: int = 30,
    include_synthons: bool = False,
    api_key: str | None = None,
    api_url: str | None = None,
    profile: str | None = None,
    credential_provider: CredentialSource | None = None,
    timeout: float = 45.0,
    transport: Any = None,
) -> SubstructureResult:
    """Run one exact substructure operation and close its internal transport."""

    with Client(
        **_client_options(
            api_key=api_key,
            api_url=api_url,
            profile=profile,
            credential_provider=credential_provider,
            timeout=timeout,
            transport=transport,
        )
    ) as client:
        return client.search_substructure(
            query,
            query_format=format,
            database=database,
            limit=limit,
            timeout_seconds=timeout_seconds,
            include_synthons=include_synthons,
        )


def sample(
    *,
    database: str,
    count: int = 100,
    seed: int | None = None,
    include_synthons: bool = False,
    api_key: str | None = None,
    api_url: str | None = None,
    profile: str | None = None,
    credential_provider: CredentialSource | None = None,
    timeout: float = 45.0,
    transport: Any = None,
) -> SampleResult:
    """Sample one chemical space and close the internal transport."""

    with Client(
        **_client_options(
            api_key=api_key,
            api_url=api_url,
            profile=profile,
            credential_provider=credential_provider,
            timeout=timeout,
            transport=transport,
        )
    ) as client:
        return client.sample(
            database=database,
            count=count,
            seed=seed,
            include_synthons=include_synthons,
        )


def catalog(
    *,
    api_key: str | None = None,
    api_url: str | None = None,
    profile: str | None = None,
    credential_provider: CredentialSource | None = None,
    timeout: float = 45.0,
    transport: Any = None,
) -> dict[str, Any]:
    """Return the public catalog and close the internal transport."""

    with Client(
        **_client_options(
            api_key=api_key,
            api_url=api_url,
            profile=profile,
            credential_provider=credential_provider,
            timeout=timeout,
            transport=transport,
        )
    ) as client:
        return client.catalog()


def usage(
    *,
    api_key: str | None = None,
    api_url: str | None = None,
    profile: str | None = None,
    credential_provider: CredentialSource | None = None,
    timeout: float = 45.0,
    transport: Any = None,
) -> Usage:
    """Return the account plan and remaining daily credits, then close the transport."""

    with Client(
        **_client_options(
            api_key=api_key,
            api_url=api_url,
            profile=profile,
            credential_provider=credential_provider,
            timeout=timeout,
            transport=transport,
        )
    ) as client:
        return client.usage()
