"""Async convenience operations backed by one short-lived client each."""

from __future__ import annotations

from typing import Any

from .client import AsyncClient
from .models import SampleResult, SearchResult, SubstructureResult


async def search(smiles: str, *, database: str, method: str = "morgan", **kwargs) -> SearchResult:
    async with AsyncClient(**_connection_options(kwargs)) as client:
        return await client.search(smiles, database=database, method=method, **kwargs)


async def substructure(
    query: str, *, database: str, format: str = "smarts", **kwargs
) -> SubstructureResult:
    async with AsyncClient(**_connection_options(kwargs)) as client:
        return await client.search_substructure(
            query, database=database, query_format=format, **kwargs
        )


async def sample(*, database: str, **kwargs) -> SampleResult:
    async with AsyncClient(**_connection_options(kwargs)) as client:
        return await client.sample(database=database, **kwargs)


async def catalog(**kwargs) -> dict[str, Any]:
    async with AsyncClient(**_connection_options(kwargs)) as client:
        return await client.catalog()


def _connection_options(kwargs: dict[str, Any]) -> dict[str, Any]:
    names = {"api_key", "api_url", "profile", "credential_provider", "timeout", "transport"}
    return {name: kwargs.pop(name) for name in tuple(kwargs) if name in names}
