"""Thin wire builders for server-side automatic database selection."""

import re

from .databases import resolve_database
from .subsets import search_filters, similarity_selection


def synthesis_days(value):
    """Parse a positive business-day limit; no ambiguous month conversions."""
    if value is None:
        return None
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d+)\s*(?:d|days?)\s*", value, re.IGNORECASE)
        if not match:
            raise ValueError("synthesis must be a business-day limit, e.g. '7d' or '7 days'")
        value = int(match.group(1))
    if type(value) is not int or not 1 <= value <= 3650:
        raise ValueError("synthesis must be between 1 and 3650 business days")
    return value


def routing_database(database, *, synthesis=None, include_catalogues=False, databases=None):
    synthesis_days(synthesis)
    if type(include_catalogues) is not bool:
        raise ValueError("include_catalogues must be a boolean")
    if database != "auto":
        if include_catalogues:
            raise ValueError("include_catalogues/--all requires automatic database selection")
        if synthesis is not None:
            if databases is not None:
                raise ValueError("use either database or databases with synthesis")
            # A constrained explicit source uses the same authoritative server planner.
            return "auto", [database], True
    return database, databases, include_catalogues


def auto_options(
    *, availability=None, scope="orderable", databases=None, timeout_seconds=30,
    synthesis=None, include_catalogues=False,
):
    maximum = synthesis_days(synthesis)
    if type(include_catalogues) is not bool:
        raise ValueError("include_catalogues must be a boolean")
    if availability not in {None, "in_stock", "make_on_demand", "research", "hypothetical"}:
        raise ValueError("unknown availability")
    if scope not in {"orderable", "all"}:
        raise ValueError("scope must be 'orderable' or 'all'")
    if not 1 <= timeout_seconds <= 120:
        raise ValueError("timeout_seconds must be between 1 and 120")
    if databases is not None and (isinstance(databases, str) or not 1 <= len(databases) <= 32):
        raise ValueError("databases must be a sequence of 1 to 32 database names")
    return {
        "max_synthesis_days": maximum,
        "include_catalogues": include_catalogues,
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
    synthesis=None,
    include_catalogues=False,
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
            synthesis=synthesis, include_catalogues=include_catalogues,
            availability=availability,
            scope=scope,
            databases=databases,
            timeout_seconds=timeout_seconds,
        ),
    }
