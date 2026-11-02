"""Thin wire builders for server-side automatic database selection."""

from .databases import resolve_database
from .subsets import search_filters, similarity_selection


def auto_options(*, availability=None, scope="orderable", databases=None, timeout_seconds=30):
    if availability not in {None, "in_stock", "make_on_demand", "research", "hypothetical"}:
        raise ValueError("unknown availability")
    if scope not in {"orderable", "all"}:
        raise ValueError("scope must be 'orderable' or 'all'")
    if not 1 <= timeout_seconds <= 120:
        raise ValueError("timeout_seconds must be between 1 and 120")
    if databases is not None and (isinstance(databases, str) or not 1 <= len(databases) <= 32):
        raise ValueError("databases must be a sequence of 1 to 32 database names")
    return {
        "availability": availability,
        "scope": scope,
        "databases": [resolve_database(value) for value in databases] if databases else None,
        "timeout_seconds": timeout_seconds,
    }


def require_auto(database, *, availability=None, scope="orderable", databases=None):
    if database != "auto" and (
        availability is not None or scope != "orderable" or databases is not None
    ):
        raise ValueError("availability, scope and databases require database='auto'")


def similarity_plan(
    smiles,
    *,
    method="morgan",
    limit=20,
    include_synthons=False,
    availability=None,
    scope="orderable",
    databases=None,
    timeout_seconds=30,
    **filters,
):
    _, filters = search_filters("auto", **filters)
    selection = similarity_selection(
        smiles,
        database="auto",
        filters=filters,
        method=method,
        limit=limit,
        include_synthons=include_synthons,
    )
    return {
        "selection": selection.to_dict(),
        **auto_options(
            availability=availability,
            scope=scope,
            databases=databases,
            timeout_seconds=timeout_seconds,
        ),
    }
