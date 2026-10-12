import asyncio
import json

import httpx
import pytest

from deepmedchem import AsyncClient, Client, Run, Selection
from deepmedchem.ordering import read_order_csv


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "alias,expected",
    [
        ("cheminfinita", "cheminfinita-2026-02"),
        ("spacem1", "d2b-spacem1"),
        ("enamine", "enamine-real-v5a"),
        ("freedom", "freedom-space-5"),
        ("explore", "synple-explore-2025-10"),
        ("synple", "synple-synple-2025-10"),
        ("vast", "vast-2026-h2"),
        ("ENAMINE", "enamine-real-v5a"),
        ("enamine-real-v5a", "enamine-real-v5a"),
        ("Private-Space", "Private-Space"),
    ],
)
def test_aliases_in_all_search_request_payloads(asynchronous, alias, expected):
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    options = dict(
        api_key="test", api_url="https://example.test", transport=httpx.MockTransport(handler)
    )

    async def run_async():
        async with AsyncClient(**options) as client:
            await client.search("CCO", database=alias)
            await client.search("CCO", database=alias, method="shape")
            await client.search_cheese("CCO", database=alias, scorer="esp")
            await client.search_substructure("CO", database=alias)
            await client.sample(database=alias)

    if asynchronous:
        asyncio.run(run_async())
    else:
        with Client(**options) as client:
            client.search("CCO", database=alias)
            client.search("CCO", database=alias, method="shape")
            client.search_cheese("CCO", database=alias, scorer="esp")
            client.search_substructure("CO", database=alias)
            client.sample(database=alias)
    assert len(payloads) == 5
    assert all(p["database_id"] == expected for p in payloads)


def test_selection_and_run_templates_expand_alias_without_changing_release():
    selection = Selection.from_database("enamine", release="custom-release").sample(seed=7)
    assert selection.to_dict()["database"] == {
        "database_id": "enamine-real-v5a",
        "release_id": "custom-release",
    }
    run = Run.selection_batch(template=selection, items={"one": {}})
    assert "enamine-real-v5a" in json.dumps(run.to_dict())


def test_order_csv_abbreviation_and_fallback(tmp_path):
    path = tmp_path / "molecules.csv"
    path.write_text("smiles,database_id\nCCO,enamine\n")
    assert read_order_csv(path)[0].database_id == "enamine-real-v5a"
    path.write_text("smiles\nCCO\n")
    assert read_order_csv(path, database="freedom")[0].database_id == "freedom-space-5"


@pytest.mark.parametrize(
    "database",
    [
        "molport",
        "mcule-in-stock",
        "MCULE-FULL",
        "chemspace-screening",
        "zinc15",
        "enamine-real",
        "chemspace-5b-ro5",
        "chemspace-5b-beyond-ro5",
        "chemspace-5b-freedom",
        "explore-enumerated",
        "explore-diverse",
        "explore-5p4b",
        "synple-4b",
        "xtalpi",
        "chemriya",
        "molecule-one",
        "enamine-aa",
        "mcule-aa",
        "mcule-aa-in-stock",
        "mcule-unnatural-aa",
        "mcule-unnatural-aa-in-stock",
    ],
)
def test_retired_cheese_databases_and_aliases_are_rejected(database):
    from deepmedchem.databases import RETIRED_DATABASES, resolve_database

    with pytest.raises(ValueError, match=f"{database.upper()} was a classic CHEESE Search"):
        resolve_database(database)
    assert len(RETIRED_DATABASES) == 21


def test_platform_and_private_databases_resolve_verbatim():
    from deepmedchem.databases import DATABASE_DETAILS, resolve_database

    assert resolve_database("enamine-real-v5a") == "enamine-real-v5a"
    assert resolve_database("Private-Space") == "Private-Space"
    assert all("served_by" not in info for info in DATABASE_DETAILS.values())


def test_order_csvs_from_cheese_exports_keep_their_database_ids(tmp_path):
    path = tmp_path / "molecules.csv"
    path.write_text("smiles,database_id\nCCO,MCULE-IN-STOCK\n")
    assert read_order_csv(path)[0].database_id == "MCULE-IN-STOCK"
