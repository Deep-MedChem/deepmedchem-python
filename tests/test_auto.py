import asyncio
import json

import httpx
import pytest

import deepmedchem as dmc


def transport(captured):
    def handle(request):
        captured.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "database_id": "auto",
                "results": [
                    {
                        "smiles": "CCO",
                        "score": 1,
                        "sources": [{"database_id": "enamine-real-v5a", "price": 100}],
                    }
                ],
                "routing": {"policy": "all-eligible-v1", "credits": 1},
                "all_sources_searched": False,
            },
        )

    return httpx.MockTransport(handle)


def test_auto_similarity_sync_async_and_facade_keep_filters_and_aliases():
    captured = []
    options = dict(
        database="auto:primary-amines",
        method="shape",
        max_price=200,
        where="mw<=450",
        availability="make_on_demand",
        databases=["enamine", "vast"],
    )
    connection = dict(api_key="test", transport=transport(captured))
    with dmc.Client(**connection) as client:
        result = client.search("CCO", **options)
    assert result.routing["credits"] == 1
    assert result.hits[0].sources[0]["price"] == 100
    assert not result.all_sources_searched
    dmc.search("CCO", **options, **connection)

    async def run():
        async with dmc.AsyncClient(**connection) as client:
            return await client.search("CCO", **options)

    asyncio.run(run())
    assert captured[0] == captured[1] == captured[2]
    path, payload = captured[0]
    assert path == "/api/v2/search_auto"
    assert payload["databases"] == ["enamine-real-v5a", "vast-2026-h2"]
    assert payload["selection"]["constraints"]["price"]["max"] == 200
    assert payload["selection"]["constraints"]["structures"]["all"]


def test_substructure_sync_async_and_facade():
    captured = []
    connection = dict(api_key="test", transport=transport(captured))
    options = dict(database="auto", availability="in_stock", scope="all", timeout_seconds=12)
    with dmc.Client(**connection) as client:
        result = client.search_substructure("CO", **options)
    dmc.substructure("CO", **options, **connection)

    async def run():
        async with dmc.AsyncClient(**connection) as client:
            return await client.search_substructure("CO", **options)

    asyncio.run(run())
    assert result.method == "substructure"
    assert all(path == "/api/v2/search_auto" for path, _ in captured)
    for _, payload in captured:
        assert payload["timeout_seconds"] == 12
        assert payload["substructure"]["query"] == {"format": "smarts", "value": "CO"}


def test_plan_is_a_separate_free_operation():
    captured = []
    with dmc.Client(api_key="test", transport=transport(captured)) as client:
        client.plan_search("CCO", max_price=200)
    assert captured[0][0] == "/api/v2/search_auto:plan"


@pytest.mark.parametrize(
    "options",
    [
        {"availability": "in_stock"},
        {"scope": "all"},
        {"databases": ["enamine"]},
        {"timeout_seconds": 60},
    ],
)
def test_routing_options_are_not_silently_ignored_for_explicit_database(options):
    captured = []
    with dmc.Client(api_key="test", transport=transport(captured)) as client:
        with pytest.raises(ValueError):
            client.search("CCO", database="enamine", **options)
    assert not captured


def test_existing_cheese_entrypoint_accepts_auto():
    captured = []
    with dmc.Client(api_key="test", transport=transport(captured)) as client:
        client.search_cheese("CCO", database="auto", scorer="esp")
    assert captured[0][1]["selection"]["objectives"][0]["metric_id"] == "cheese.electrostatic"


def test_export_preserves_per_hit_database_and_sources(tmp_path):
    import csv

    result = dmc.SearchResult(database_id="auto", results=[{
        "smiles": "CCO", "database_id": "enamine-real-v5a", "database_release": "release",
        "product_id": "p", "sources": [{"database_id": "enamine-real-v5a"}],
    }])
    path = tmp_path / "hits.csv"
    result.to_csv(path)
    with path.open() as stream:
        row = next(csv.DictReader(stream))
    assert row["database_id"] == "enamine-real-v5a"
    assert json.loads(row["sources"])[0]["database_id"] == "enamine-real-v5a"


def test_auto_search_and_plan_reuse_typed_constraints():
    captured = []
    connection = dict(api_key="test", transport=transport(captured))
    filters = dict(
        where=dmc.PropertyRange("MolWt", max=400),
        require=dmc.RequireStructure("alpha-amino-acid/v1"),
        exclude=dmc.ExcludeStructure(smarts="N", min_count=2),
    )
    with dmc.Client(**connection) as client:
        client.search("CCO", database="auto", **filters)
        client.plan_search("CCO", **filters)
    dmc.search("CCO", database="auto", **filters, **connection)

    async def run():
        async with dmc.AsyncClient(**connection) as client:
            await client.search("CCO", database="auto", **filters)
            await client.plan_search("CCO", **filters)

    asyncio.run(run())
    assert captured[1][0] == captured[4][0] == "/api/v2/search_auto:plan"
    constraints = captured[0][1]["selection"]["constraints"]
    assert all(item[1]["selection"]["constraints"] == constraints for item in captured)
    assert constraints["properties"][0]["value"] == 400
    assert constraints["structures"]["all"][0]["pattern_id"] == "alpha-amino-acid/v1"
    assert constraints["structures"]["none"][0]["count"] == {"min": 2}


def test_substructure_omitted_database_matches_explicit_auto_sync_async_and_facade():
    captured = []
    connection = dict(api_key="test", transport=transport(captured))
    with dmc.Client(**connection) as client:
        client.search_substructure("CO")
        client.search_substructure("CO", database="auto")
    dmc.substructure("CO", **connection)

    async def run():
        async with dmc.AsyncClient(**connection) as client:
            await client.search_substructure("CO")
    asyncio.run(run())
    assert all(item == captured[0] for item in captured)
    assert captured[0][0] == "/api/v2/search_auto"
