import asyncio
import json

import httpx
import pytest

import deepmedchem as dmc
from deepmedchem.auto import similarity_plan
from deepmedchem.optimization import build_specification


def native_query():
    return dmc.StructureQuery(
        expression=dmc.AllOf(
            conditions=(
                dmc.Contains(smarts="N"),
                dmc.AnyOf(conditions=(dmc.Contains(smarts="O"), dmc.Contains(smarts="S"))),
                dmc.Not(condition=dmc.Contains(smarts="Cl")),
            )
        )
    )


def test_query_reuse_across_selection_optimization_and_routing():
    query = native_query()
    expected = query.model_dump(mode="json", exclude_none=True)
    selection = dmc.Selection.from_database("enamine").constrain(query)
    assert selection.to_dict()["constraints"]["structures"]["query"] == expected
    spec = build_specification(
        database="enamine", budget=10, batch_size=5, direction="maximize", constraints=[query]
    )
    assert spec["constraints"]["structures"]["query"] == expected
    plan = similarity_plan("CC", structure_query=query)
    assert plan["selection"]["constraints"]["structures"]["query"] == expected
    detached = selection.to_dict()
    detached["constraints"]["structures"]["query"]["stereo"] = "ignore"
    assert query.stereo == "strict"
    assert selection.to_dict()["constraints"]["structures"]["query"] == expected


def test_native_constraints_compose_and_reject_mixed_stereo():
    selection = dmc.Selection.from_database("enamine").constrain(dmc.Contains(smarts="N"))
    combined = selection.constrain(dmc.Contains(smarts="O")).to_dict()
    assert combined["constraints"]["structures"]["query"]["expression"]["type"] == "all"
    with pytest.raises(ValueError, match="stereo"):
        selection.constrain(
            dmc.StructureQuery(expression=dmc.Contains(smarts="O"), stereo="ignore")
        )


@pytest.mark.parametrize("auto", [False, True])
def test_sync_async_substructure_wire(auto):
    captured = []

    def handler(request):
        captured.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"results": []})

    options = dict(
        api_key="test", api_url="https://example.test", transport=httpx.MockTransport(handler)
    )
    database = "auto" if auto else "enamine"
    with dmc.Client(**options) as client:
        client.search_substructure(native_query(), database=database)

    async def run():
        async with dmc.AsyncClient(**options) as client:
            await client.search_substructure(native_query(), database=database)

    asyncio.run(run())
    for path, body in captured:
        assert path == ("/api/v2/search_auto" if auto else "/api/v2/search_substructure")
        payload = body["substructure"] if auto else body
        assert "query" not in payload
        assert payload["structure_query"] == native_query().model_dump(
            mode="json", exclude_none=True
        )


def test_substructure_filters_keep_strict_bounds_and_compose():
    from deepmedchem.client import _substructure_query

    payload = _substructure_query(
        native_query(),
        "smarts",
        where=["mw<400", "mw>=100"],
        require="F",
        exclude="Br",
        max_price=250,
    )
    assert payload["property_constraints"]["MolWt"] == {
        "max": 400,
        "max_inclusive": False,
        "min": 100,
        "min_inclusive": True,
    }
    assert payload["price"] == {"max": 250, "missing": "reject"}
    assert payload["structure_query"]["expression"]["type"] == "all"
    with pytest.raises(ValueError, match="contradictory"):
        _substructure_query("C", "smarts", where=["mw<100", "mw>=100"])


def test_sync_async_native_catalogue_sampling_wire():
    captured = []

    def handler(request):
        captured.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={
            "id": "selection-test", "object": "selection", "status": "completed",
            "selection_hash": "test", "normalized_selection": json.loads(request.content),
            "results": [],
        })

    options = dict(api_key="test", transport=httpx.MockTransport(handler))
    with dmc.Client(**options) as client:
        client.sample(database="mcule-aa", count=3, structure_query=native_query())

    async def run():
        async with dmc.AsyncClient(**options) as client:
            await client.sample(database="mcule-aa", count=3, structure_query=native_query())

    asyncio.run(run())
    for path, payload in captured:
        assert path == "/api/v2/selections"
        assert payload["strategy"]["type"] == "sample"
        assert payload["constraints"]["structures"]["query"] == native_query().model_dump(
            mode="json", exclude_none=True
        )
