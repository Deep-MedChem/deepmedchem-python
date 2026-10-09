import asyncio
import json

import httpx
import pytest

from deepmedchem import AsyncClient, Client, Run, Selection, __version__
from deepmedchem.client import DeepMedChemError
from deepmedchem.models import SampleResult, SearchResult, SubstructureResult


def _run_resource(status="queued"):
    terminal = status in {"completed", "completed_with_errors", "failed", "cancelled"}
    return {
        "id": "run_123",
        "object": "run",
        "kind": "selection_batch",
        "status": status,
        "progress": {
            "total": 1,
            "pending": 0 if terminal else 1,
            "running": 0,
            "succeeded": 1 if status == "completed" else 0,
            "failed": 0,
            "cancelled": 0,
        },
        "last_event_sequence": 1,
        "links": {},
    }


def test_simple_methods_use_distinct_public_operations() -> None:
    captured = []

    def handler(request: httpx.Request):
        captured.append((request.method, request.url.path, json.loads(request.read() or b"{}")))
        return httpx.Response(200, json={"results": []})

    with Client(
        api_key="scoped-token",
        api_url="https://example.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        client.search("CCO", database="enamine-real-v5a")
        client.search_cheese("CCO", database="enamine-real-v5a", scorer="shape")
        client.search_substructure("C(=O)N", query_format="smarts", database="enamine-real-v5a")
        client.sample(database="enamine-real-v5a", count=10, seed=42)

    assert [value[1] for value in captured] == [
        "/api/v2/search",
        "/api/v2/search_cheese",
        "/api/v2/search_substructure",
        "/api/v2/sample",
    ]
    assert "scorer" not in captured[0][2]
    assert captured[1][2]["scorer"] == "shape"
    assert captured[2][2]["query"] == {"format": "smarts", "value": "C(=O)N"}
    assert captured[3][2]["seed"] == 42


def test_client_attribution_is_configurable() -> None:
    def handler(request: httpx.Request):
        assert request.headers["x-dmc-client"] == "navigator-cli"
        assert request.headers["x-dmc-client-version"] == "0.4.0"
        assert request.headers["x-dmc-sdk-version"] == __version__
        assert request.headers["user-agent"].startswith("navigator-cli/0.4.0 ")
        assert f"deepmedchem/{__version__}" in request.headers["user-agent"]
        return httpx.Response(200, json={"spaces": []})

    with Client(
        api_key="token",
        api_url="https://example.test",
        application="navigator-cli",
        application_version="0.4.0",
        transport=httpx.MockTransport(handler),
    ) as client:
        client.catalog()


def test_safe_request_retries_transient_service_response() -> None:
    attempts = 0

    def handler(request: httpx.Request):
        nonlocal attempts
        if request.url.path != "/api/v2/catalog":
            # catalog() also asks classic CHEESE for its databases; not under test here.
            return httpx.Response(200, json={})
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"error": {"code": "overloaded"}})
        return httpx.Response(200, json={"libraries": []})

    with Client(
        api_key="token",
        api_url="https://example.test",
        max_retries=1,
        retry_backoff=0,
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.catalog() == {"libraries": []}
    assert attempts == 2


def test_selection_builder_is_copy_on_write_and_round_trips() -> None:
    base = Selection.from_database("enamine-real-v5a").ranked()
    aspirin = (
        base.reference("aspirin", smiles="CC(=O)Oc1ccccc1C(=O)O")
        .maximize_similarity("rdkit.ecfp4_tanimoto", reference="aspirin")
        .require_different_scaffold("rdkit.bemis_murcko", reference="aspirin")
        .require_pattern("alpha-amino-acid/v1", min_count=1)
        .where("rdkit.mol_wt", gt=250, units="Da")
        .limit(100)
        .max_per_scaffold(5)
        .include("properties", "constraint_evidence", "execution_plan")
    )
    assert base.to_dict()["references"] == []
    payload = aspirin.to_dict()
    assert payload["constraints"]["properties"][0]["operator"] == "gt"
    assert payload["constraints"]["relationships"][0]["operator"] == "different"
    assert "shortlist_multiplier" not in payload["execution"]
    assert Selection.model_validate(json.loads(aspirin.to_json())).to_dict() == payload
    loaded_yaml = __import__("yaml").safe_load(aspirin.to_yaml())
    assert Selection.model_validate(loaded_yaml).to_dict() == payload


def test_selection_accepts_normalized_unpinned_database_release() -> None:
    payload = Selection.from_database("enamine-real-v5a").sample(seed=42).to_dict()
    payload["database"]["release_id"] = None
    assert Selection.model_validate(payload).to_dict()["database"]["release_id"] is None


def _combined_selection() -> Selection:
    return (
        Selection.from_database("enamine-real-v5a")
        .reference("query", smiles="CCO")
        .maximize_similarity("rdkit.ecfp4_tanimoto", reference="query")
        .require_preset("lipinski-ro5/v1")
        .where("rdkit.mol_wt", lte=450, units="Da")
        .acquire_predicted_property(
            "openadmet-herg-pchembl",
            direction="minimize",
            keep_fraction=0.25,
        )
        .include("properties", "objective_components")
        .limit(100)
    )


def test_selection_builds_exact_predicted_property_range() -> None:
    selection = (
        Selection.from_database("enamine-real-v5a")
        .reference("query", smiles="CCO")
        .maximize_similarity("rdkit.ecfp4_tanimoto", reference="query")
        .where_predicted_property(
            "openadmet-herg-pchembl", units="pChEMBL", lte=5.0
        )
    )

    condition = selection.to_dict()["constraints"]["predicted_properties"][0]
    assert condition["required_fidelity"] == "pinned_assembled_product_teacher"
    assert condition["operator"] == "lte"
    assert condition["value"] == 5.0


def _selection_response(selection: Selection) -> dict:
    return {
        "id": "sel_1",
        "object": "selection",
        "status": "completed",
        "selection_hash": "abc",
        "normalized_selection": selection.to_dict(),
        "results": [
            {
                "rank": 1,
                "smiles": "CCO",
                "properties": {"MolWt": 46.07},
                "acquisition": {
                    "endpoint_id": "openadmet-herg-pchembl",
                    "approximate_value": 4.0,
                    "predicted_value": 4.2,
                    "applicable": True,
                },
            }
        ],
        "acquisition": {
            "endpoint_id": "openadmet-herg-pchembl",
            "approximate_model_version": "herg-cp16@abc123",
            "predicted_model_version": "herg-teacher@def456",
            "direction": "minimize",
            "units": "pChEMBL",
            "qualification": "experimental-acquisition-only",
            "candidates_before": 400,
            "candidates_after": 100,
        },
    }


def test_sync_selection_emits_combined_document_and_models_acquisition() -> None:
    selection = _combined_selection()

    def handler(request: httpx.Request):
        assert json.loads(request.content) == selection.to_dict()
        return httpx.Response(200, json=_selection_response(selection))

    with Client(
        api_key="token",
        api_url="https://example.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = client.selections.create(selection)

    assert result.acquisition.qualification == "experimental-acquisition-only"
    assert result.hits[0].acquisition.predicted_value == 4.2
    assert result.hits[0].properties == {"MolWt": 46.07}


def test_async_selection_emits_the_same_combined_document() -> None:
    async def scenario():
        selection = _combined_selection()

        async def handler(request: httpx.Request):
            assert json.loads(request.content) == selection.to_dict()
            return httpx.Response(200, json=_selection_response(selection))

        async with AsyncClient(
            api_key="token",
            api_url="https://example.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.selections.create(selection)
        assert result.acquisition.candidates_before == 400

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "arguments",
    [
        {"endpoint_id": "", "direction": "minimize", "keep_fraction": 0.25},
        {"endpoint_id": "herg", "direction": "lower", "keep_fraction": 0.25},
        {"endpoint_id": "herg", "direction": "minimize", "keep_fraction": 0},
        {"endpoint_id": "herg", "direction": "minimize", "keep_fraction": 1.1},
    ],
)
def test_acquisition_builder_validates_arguments_locally(arguments) -> None:
    selection = (
        Selection.from_database("db")
        .reference("query", smiles="CCO")
        .maximize_similarity("rdkit.ecfp4_tanimoto", reference="query")
    )
    with pytest.raises(ValueError):
        selection.acquire_predicted_property(**arguments)


def test_run_builder_creates_shared_template_and_unique_bindings() -> None:
    template = (
        Selection.from_database("enamine-real-v5a")
        .ranked()
        .maximize_similarity("rdkit.ecfp4_tanimoto", reference="query")
        .limit(10)
    )
    run = Run.selection_batch(
        template=template,
        items={"lead-001": {"query": "CCO"}, "lead-002": {"query": "CCN"}},
    )
    payload = run.to_dict()
    assert payload["schema_version"] == "run/1"
    assert payload["selection_template"]["references"] == []
    assert [item["id"] for item in payload["items"]] == ["lead-001", "lead-002"]


def test_runs_namespace_requires_idempotency_and_follows_result_cursors() -> None:
    paths = []

    def handler(request: httpx.Request):
        paths.append(str(request.url))
        if request.url.path == "/api/v2/runs":
            assert request.headers["idempotency-key"] == "campaign-1"
            return httpx.Response(202, json=_run_resource())
        if request.url.params.get("cursor") == "next":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "b",
                            "input_index": 1,
                            "status": "failed",
                            "error": {"code": "invalid_smiles"},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "a",
                        "input_index": 0,
                        "status": "succeeded",
                        "result": {"results": []},
                    }
                ],
                "next_cursor": "next",
            },
        )

    selection = Selection.from_database("db").sample().limit(10)
    with Client(
        api_key="token",
        api_url="https://example.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        run = client.runs.create(Run.selection(selection), idempotency_key="campaign-1")
        results = list(client.runs.iter_results(run.id))
    assert [item.id for item in results] == ["a", "b"]
    assert results[0].ok is True
    assert results[1].ok is False
    assert any("cursor=next" in value for value in paths)


def test_async_client_has_matching_simple_search() -> None:
    async def scenario():
        async def handler(request: httpx.Request):
            assert request.url.path == "/api/v2/search"
            return httpx.Response(200, json={"results": [], "request_id": "req_1"})

        async with AsyncClient(
            api_key="token",
            api_url="https://example.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.search("CCO", database="db")
            assert result.request_id == "req_1"

    asyncio.run(scenario())


def test_client_search_method_routes_without_changing_morgan_default() -> None:
    paths = []

    def handler(request: httpx.Request):
        paths.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={"results": [], "scorer": paths[-1][1].get("scorer", "morgan")},
        )

    with Client(
        api_key="token",
        api_url="https://example.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.search("CCO", database="db").method == "morgan"
        assert client.search("CCO", database="db", method="shape").method == "shape"

    assert [path for path, _ in paths] == ["/api/v2/search", "/api/v2/search_cheese"]


def test_lightweight_result_is_an_aligned_molecule_sequence() -> None:
    result = SearchResult.model_validate(
        {
            "request_id": "req_1",
            "database_id": "db",
            "database_release": "2026.09",
            "scorer": "shape",
            "metric": "CHEESE shape cosine",
            "counts": {"returned": 2},
            "timing_ms": {"total": 12.5},
            "new_server_field": {"kept": True},
            "results": [
                {
                    "rank": 1,
                    "smiles": "CCO",
                    "score": 0.9,
                    "product_id": "p1",
                    "price": 163,
                },
                {"rank": 2, "smiles": "CCN", "score": None, "new_hit_field": 7},
            ],
        }
    )

    assert list(result) == result.smiles == ["CCO", "CCN"]
    assert result[0] == "CCO"
    assert result[:] == ["CCO", "CCN"]
    assert result.scores == [0.9, None]
    assert result.prices == [163, None]
    assert result.hits[0].price == 163
    assert result.ids == ["p1", None]
    assert result.hits[1].extra == {"new_hit_field": 7}
    assert result.meta.release == "2026.09"
    assert result.meta.elapsed_ms == 12.5
    assert result.raw["new_server_field"] == {"kept": True}
    assert result.results[0]["smiles"] == "CCO"
    assert repr(result) == "SearchResult(2 molecules, method='shape', database='db')"


def test_exact_results_keep_aligned_none_scores() -> None:
    payload = {"database_id": "db", "results": [{"index": 0, "smiles": "CCO"}]}
    sample = SampleResult.model_validate(payload)
    substructure = SubstructureResult.model_validate(payload)
    assert sample.scores == [None]
    assert substructure.scores == [None]
    assert sample.method == "sample"
    assert substructure.method == "substructure"


def test_error_does_not_expose_api_key() -> None:
    def handler(_request: httpx.Request):
        return httpx.Response(
            401,
            json={
                "error": {
                    "code": "unauthorized",
                    "message": "A valid API key is required.",
                    "request_id": "req_1",
                }
            },
        )

    client = Client(
        api_key="super-secret",
        api_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        try:
            client.catalog()
        except DeepMedChemError as error:
            assert error.code == "unauthorized"
            assert error.request_id == "req_1"
            assert "super-secret" not in str(error)
        else:
            raise AssertionError("expected DeepMedChemError")
    finally:
        client.close()


def test_usage_calls_the_account_service_with_the_same_key() -> None:
    captured = []

    def handler(request: httpx.Request):
        captured.append(request)
        return httpx.Response(
            200,
            json={"plan": "premium", "limit": 100, "used": 5, "remaining": 95, "unlimited": False},
        )

    transport = httpx.MockTransport(handler)
    with Client(
        api_key="scoped-token",
        api_url="https://api.example.test",
        account_url="https://account.example.test/",
        transport=transport,
    ) as client:
        usage = client.usage()

    async def scenario():
        async with AsyncClient(
            api_key="scoped-token",
            api_url="https://api.example.test",
            account_url="https://account.example.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            return await client.usage()

    async_usage = asyncio.run(scenario())
    assert usage.tier == "premium"
    assert usage.remaining == async_usage.remaining == 95
    assert repr(usage) == "Usage(plan='premium', credits=95/100 remaining)"
    assert [str(request.url) for request in captured] == [
        "https://account.example.test/rate-limit/status"
    ] * 2
    assert captured[0].headers["x-api-key"] == "scoped-token"


# --- Enumerated / in-stock databases served by classic CHEESE -----------------

CLASSIC_MOLSEARCH = {
    "canonicalized_query": "CC(=O)Oc1ccccc1C(=O)O",
    "remarks": "",
    "neighbors": [
        {"smiles": "CC(=O)Oc1ccccc1C(=O)O", "id": "MOLPORT-001-000-001", "database": "MOLPORT",
         "db_id": "1", "similarity": 1.0, "Morgan Tanimoto": 1.0},
        {"smiles": "CC(=O)Oc1ccccc1C(=O)OC", "id": "MOLPORT-002-000-002", "database": "MOLPORT",
         "db_id": "2", "similarity": 0.62, "Morgan Tanimoto": 0.62},
    ],
    "search_info": {"search_type": "morgan", "db_names": "MOLPORT", "search_id": "abc", "count": 2},
}


def _classic_client(handler, **overrides):
    options = dict(
        api_key="ck_test",
        api_url="https://api.example.test",
        account_url="https://account.example.test",
        transport=httpx.MockTransport(handler),
    )
    options.update(overrides)
    return options


def test_classic_database_search_goes_to_cheese_molsearch() -> None:
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json=CLASSIC_MOLSEARCH)

    with Client(**_classic_client(handler)) as client:
        result = client.search("CC(=O)Oc1ccccc1C(=O)O", database="molport", limit=2)

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET"
    assert request.url.host == "account.example.test"
    assert request.url.path == "/molsearch"
    params = dict(request.url.params.multi_items())
    assert params["search_input"] == "CC(=O)Oc1ccccc1C(=O)O"
    assert params["search_type"] == "morgan"
    assert params["db_names"] == "MOLPORT"
    assert params["n_neighbors"] == "2"
    assert params["descriptors"] == "false"
    assert params["properties"] == "false"
    assert request.headers["x-api-key"] == "ck_test"

    assert isinstance(result, SearchResult)
    assert list(result) == ["CC(=O)Oc1ccccc1C(=O)O", "CC(=O)Oc1ccccc1C(=O)OC"]
    assert result.scores == [1.0, 0.62]
    assert result.ids == ["MOLPORT-001-000-001", "MOLPORT-002-000-002"]
    assert result.ranks == [1, 2]
    assert result.database_id == "MOLPORT"
    assert result.method == "morgan"
    assert result.meta.returned == 2
    assert result.hits[0].extra["db_id"] == "1"
    assert result.prices == [None, None]


@pytest.mark.parametrize(
    "method,expected",
    [("shape", "espsim_shape"), ("esp", "espsim_electrostatic")],
)
def test_classic_database_shape_and_esp_map_to_cheese_search_types(method, expected) -> None:
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json=CLASSIC_MOLSEARCH)

    with Client(**_classic_client(handler)) as client:
        result = client.search("CCO", database="MCULE-IN-STOCK", method=method)
    params = dict(seen[0].url.params.multi_items())
    assert seen[0].url.path == "/molsearch"
    assert params["search_type"] == expected
    assert params["db_names"] == "MCULE-IN-STOCK"
    assert result.method == method


def test_classic_database_limit_is_capped_at_cheese_maximum() -> None:
    def handler(request: httpx.Request):  # pragma: no cover - must not be reached
        raise AssertionError("no request expected")

    with Client(**_classic_client(handler)) as client:
        with pytest.raises(ValueError, match="100"):
            client.search("CCO", database="molport", limit=101)


def test_classic_database_rejects_substructure_and_sampling() -> None:
    def handler(request: httpx.Request):  # pragma: no cover - must not be reached
        raise AssertionError("no request expected")

    with Client(**_classic_client(handler)) as client:
        with pytest.raises(DeepMedChemError) as substructure:
            client.search_substructure("C(=O)N", database="molport")
        with pytest.raises(DeepMedChemError) as sample:
            client.sample(database="molport")
    assert substructure.value.code == "unsupported_operation"
    assert "MOLPORT" in str(substructure.value)
    assert sample.value.code == "unsupported_operation"


def test_platform_databases_still_use_platform_search() -> None:
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json={"results": []})

    with Client(**_classic_client(handler)) as client:
        client.search("CCO", database="enamine")
    assert seen[0].url.host == "api.example.test"
    assert seen[0].url.path == "/api/v2/search"


def test_async_classic_database_search_goes_to_cheese_molsearch() -> None:
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json=CLASSIC_MOLSEARCH)

    async def run():
        async with AsyncClient(**_classic_client(handler)) as client:
            result = await client.search("CCO", database="molport", method="shape", limit=5)
            with pytest.raises(DeepMedChemError):
                await client.sample(database="molport")
            return result

    result = asyncio.run(run())
    assert seen[0].url.path == "/molsearch"
    params = dict(seen[0].url.params.multi_items())
    assert params["search_type"] == "espsim_shape"
    assert params["n_neighbors"] == "5"
    assert result.database_id == "MOLPORT"
    assert len(result) == 2


def test_catalog_merges_classic_databases_from_cheese() -> None:
    def handler(request: httpx.Request):
        if request.url.path == "/api/v2/catalog":
            return httpx.Response(200, json={"libraries": [{"database_id": "enamine-real-v5a"}]})
        assert request.url.host == "account.example.test"
        assert request.url.path == "/available_databases_full"
        return httpx.Response(200, json={
            "MOLPORT": {"Vendor": "Molport", "Website": "https://molport.com/",
                        "Email": "sales@molport.com", "Number of molecules": "5900000"},
            "ZINC15": {"Vendor": "N/A"},
            "MY-PRIVATE-DB": "custom_db",
            "ENAMINE-REAL-V5A-SYNTHON": {"Vendor": "Enamine", "Number of molecules": "1"},
        })

    with Client(**_classic_client(handler)) as client:
        catalog = client.catalog()

    ids = [library["database_id"] for library in catalog["libraries"]]
    assert ids == ["enamine-real-v5a", "MOLPORT", "ZINC15"]
    molport = catalog["libraries"][1]
    assert molport["served_by"] == "cheese"
    assert molport["product_count"] == 5900000
    assert molport["vendor"] == "Molport"
    assert molport["capabilities"] == {
        "search": True, "search_cheese": ["shape", "esp"], "search_substructure": False,
        "sample": False, "selections": False,
    }
    assert molport["pricing"] == {"available": False}
    assert catalog["libraries"][2]["product_count"] is None


def test_catalog_survives_cheese_outage() -> None:
    def handler(request: httpx.Request):
        if request.url.path == "/api/v2/catalog":
            return httpx.Response(200, json={"libraries": []})
        return httpx.Response(503, json={"detail": "down"})

    with Client(**_classic_client(handler, max_retries=0)) as client:
        with pytest.warns(RuntimeWarning, match="CHEESE Search catalogue unavailable"):
            catalog = client.catalog()
    assert catalog == {"libraries": []}
