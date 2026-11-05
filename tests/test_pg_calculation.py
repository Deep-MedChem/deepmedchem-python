import asyncio
import json

import httpx
import pytest

from deepmedchem import AsyncClient, Client, Selection
from deepmedchem.client import DeepMedChemError


@pytest.mark.parametrize("method", ["morgan", "shape", "esp"])
@pytest.mark.parametrize("filtered", [False, True])
@pytest.mark.parametrize("mode", ["original", "deprotected"])
def test_sync_and_async_search_forward_calculation_without_changing_defaults(
    method, filtered, mode
):
    bodies = []

    def handler(request):
        bodies.append((request.url.path, json.loads(request.content)))
        payload = {"results": [], "calculation": {"mode": mode}}
        if filtered:
            payload.update(
                id="selection-test",
                object="selection",
                status="completed",
                selection_hash="test",
                normalized_selection=bodies[-1][1],
            )
        return httpx.Response(200, json=payload)

    kwargs = dict(database="enamine-real-v5a", method=method, calculation_mode=mode)
    if filtered:
        kwargs["where"] = "mw<=450"
    with Client(
        api_key="test", api_url="https://local.test", transport=httpx.MockTransport(handler)
    ) as client:
        response = client.search("NCCO", **kwargs)
    assert response.raw["calculation"]["mode"] == mode

    async def run():
        async with AsyncClient(
            api_key="test", api_url="https://local.test", transport=httpx.MockTransport(handler)
        ) as client:
            await client.search("NCCO", **kwargs)

    asyncio.run(run())
    assert bodies[0] == bodies[1]
    path, body = bodies[0]
    assert path == (
        "/api/v2/selections"
        if filtered
        else ("/api/v2/search" if method == "morgan" else "/api/v2/search_cheese")
    )
    assert body.get("calculation_mode", "original") == mode
    if mode == "original":
        assert "calculation_mode" not in body


@pytest.mark.parametrize("operation", ["validate", "estimate", "create"])
def test_pg_exclusion_with_deprotected_mode_is_sent_unchanged_and_refused(operation):
    # Exclude and evaluate deprotected are separate choices; the server refuses the
    # combination and the SDK neither drops nor translates either part.
    mode = "deprotected"
    selection = (
        Selection.from_database("enamine-real-v5a")
        .reference("q", smiles="NCCO")
        .maximize_similarity("rdkit.ecfp4_tanimoto", reference="q")
        .calculation_mode(mode)
    ).to_dict()
    selection["constraints"] = {
        "structures": {"none": [{"pattern_id": "protecting-groups/v2"}]}
    }
    original = json.loads(json.dumps(selection))
    sent = []
    message = (
        "PG exclusion (protecting-groups/*) and calculation_mode='deprotected' are "
        "separate choices; use one of them"
    )

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(422, json={"error": {
            "code": "invalid_schema", "message": message,
            "field": "constraints.structures.none.0", "retryable": False,
        }})

    with Client(api_key="test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DeepMedChemError, match="separate choices") as error:
            getattr(client.selections, operation)(selection)
    assert error.value.status_code == 422 and not error.value.retryable

    async def run():
        async with AsyncClient(api_key="test", transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(DeepMedChemError, match="separate choices") as error:
                await getattr(client.selections, operation)(selection)
            assert error.value.status_code == 422 and not error.value.retryable

    asyncio.run(run())
    assert len(sent) == 2  # Permanent schema failures are not retried.
    assert selection == original
    for body in sent:
        assert body["constraints"] == original["constraints"]
        assert body.get("calculation_mode", "original") == mode


def test_builder_keeps_mode_in_portable_selection_and_rejects_sampling():
    original = Selection.from_database("enamine-real-v5a").reference("q", smiles="NCCO")
    parent = original.calculation_mode("deprotected")
    assert "calculation_mode" not in original.to_dict()
    assert Selection.model_validate(parent.to_dict()).to_dict()["calculation_mode"] == "deprotected"
    with pytest.raises(ValueError, match="ranked similarity"):
        parent.sample()


@pytest.mark.parametrize("mode", ["strip-everything", "", "DEPROTECTED", None, 1, [], {}])
@pytest.mark.parametrize("direct_cheese", [False, True])
def test_invalid_mode_fails_before_transport(mode, direct_cheese):
    def unexpected(request):
        pytest.fail("invalid mode reached the server")

    with Client(api_key="test", transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(ValueError, match="calculation_mode"):
            method = client.search_cheese if direct_cheese else client.search
            extra = {"scorer": "shape"} if direct_cheese else {}
            method("C", database="enamine-real-v5a", calculation_mode=mode, **extra)

    async def run():
        async with AsyncClient(api_key="test", transport=httpx.MockTransport(unexpected)) as client:
            with pytest.raises(ValueError, match="calculation_mode"):
                method = client.search_cheese if direct_cheese else client.search
                extra = {"scorer": "shape"} if direct_cheese else {}
                await method("C", database="enamine-real-v5a", calculation_mode=mode, **extra)

    asyncio.run(run())
