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
