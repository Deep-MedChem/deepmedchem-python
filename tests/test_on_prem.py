"""on_prem=True: an explicit site, no saved credentials or guest rules, local search_many."""

import asyncio
import json

import httpx
import pytest

import deepmedchem.client as client_module
from deepmedchem import AsyncClient, Client, DeepMedChemError, UnsupportedOnPremError

SITE = "http://127.0.0.1:18781"


@pytest.fixture(autouse=True)
def no_saved_settings(monkeypatch):
    """Any read of profiles, saved or environment credentials fails the test."""

    def forbidden(*_args, **_kwargs):
        raise AssertionError("on-prem clients must not read saved settings or credentials")

    monkeypatch.setattr(client_module, "load_config", forbidden)
    monkeypatch.setattr(client_module, "resolve_api_key", forbidden)
    monkeypatch.setenv("DEEPMEDCHEM_API_KEY", "cloud-key")


def search_site(seen, *, broken=frozenset()):
    """Fake site: BAD is rejected (422), EMPTY has no hits, ``broken`` SMILES drop the link."""

    def handler(request):
        seen.append((request.url.path, request.headers.get("x-api-key")))
        assert str(request.url).startswith(SITE)
        if request.url.path in {"/api/v2/catalog", "/api/v2/usage", "/api/v2/runs/r1"}:
            return httpx.Response(200, json={"unlimited": True})
        body = json.loads(request.content)
        smiles = body["query_smiles"]
        if smiles in broken:
            raise httpx.ConnectError("refused", request=request)
        if smiles == "BAD":
            return httpx.Response(
                422,
                json={"error": {"code": "invalid_smiles", "message": "cannot parse BAD"}},
                headers={"x-request-id": "req-bad"},
            )
        hits = [] if smiles == "EMPTY" else [{"smiles": smiles + "C", "score": 0.9}]
        return httpx.Response(200, json={"results": hits, "database_id": body["database_id"]})

    return httpx.MockTransport(handler)


def test_on_prem_client_uses_only_the_explicit_site_without_guest_rules():
    seen = []
    with Client(api_url=SITE, on_prem=True, transport=search_site(seen)) as client:
        assert client.is_on_prem and not client.is_guest
        client.catalog()
        # Account-only routes, which guest clients refuse locally, are sent.
        assert client.usage().unlimited
        client._request("GET", "/api/v2/runs/r1")
    assert seen == [
        ("/api/v2/catalog", None),
        ("/api/v2/usage", None),
        ("/api/v2/runs/r1", None),
    ]

    seen.clear()
    with Client("site-key", api_url=SITE, on_prem=True, transport=search_site(seen)) as client:
        client.catalog()
    assert seen == [("/api/v2/catalog", "site-key")]

    async def run():
        async with AsyncClient(api_url=SITE, on_prem=True, transport=search_site(seen)) as client:
            assert client.is_on_prem and not client.is_guest
            return await client.usage()

    seen.clear()
    assert asyncio.run(run()).unlimited
    assert seen == [("/api/v2/usage", None)]


@pytest.mark.parametrize("client_class", [Client, AsyncClient])
@pytest.mark.parametrize(
    "options",
    [
        {},
        {"api_url": "127.0.0.1:18781"},
        {"api_url": SITE, "guest": True},
        {"api_url": SITE, "profile": "dev"},
        {"api_url": SITE, "credential_provider": lambda: "key"},
    ],
)
def test_on_prem_requires_an_explicit_site_and_no_hosted_auth(client_class, options):
    with pytest.raises(ValueError, match="on_prem=True"):
        client_class(on_prem=True, **options)


def test_search_many_sends_one_search_per_query_in_order_with_errors():
    seen = []
    progress = []
    queries = [("a", "CCO"), ("bad", "BAD"), ("a", "CCO"), ("e", "EMPTY"), ("s", "c1ccccc1")]
    with Client(api_url=SITE, on_prem=True, transport=search_site(seen)) as client:
        result = client.search_many(
            queries, database="enamine-real", limit=5, on_progress=progress.append
        )
    assert [path for path, _ in seen] == ["/api/v2/search"] * 5
    assert result.run_id is None and result.status == "completed_with_errors"
    assert [(item.query.query_id, item.query.input_index) for item in result] == [
        ("a", 0),
        ("bad", 1),
        ("a", 2),
        ("e", 3),
        ("s", 4),
    ]
    assert [item.status for item in result] == ["succeeded", "failed"] + ["succeeded"] * 3
    assert result.queries[0].hits[0].smiles == "CCOC"
    assert result.queries[1].error == {
        "code": "invalid_smiles",
        "message": "cannot parse BAD",
        "status_code": 422,
        "request_id": "req-bad",
        "retryable": False,
    }
    assert [item.query.query_id for item in result.empty] == ["e"]
    assert [row["query_status"] for row in result.to_records()] == [
        "succeeded",
        "failed",
        "succeeded",
        "no_hits",
        "succeeded",
    ]
    assert progress[-1].model_dump() == {
        "total": 5,
        "pending": 0,
        "running": 0,
        "succeeded": 4,
        "failed": 1,
        "cancelled": 0,
    }


def test_search_many_bounded_concurrency_keeps_input_order():
    seen = []
    queries = {f"id{index}": "C" * (index + 1) for index in range(20)}
    with Client(api_url=SITE, on_prem=True, transport=search_site(seen)) as client:
        result = client.search_many(queries, database="enamine-real", max_concurrency=4)
        with pytest.raises(ValueError, match="max_concurrency"):
            client.search_many(queries, database="enamine-real", max_concurrency=9)
    assert result.status == "completed" and len(seen) == 20
    assert [item.query.query_id for item in result] == list(queries)
    assert [item.hits[0].smiles for item in result] == [smiles + "C" for smiles in queries.values()]


def test_unreachable_site_and_timeout_cancel_unsent_queries():
    seen = []
    transport = search_site(seen, broken={"CC"})
    with Client(api_url=SITE, on_prem=True, transport=transport, max_retries=0) as client:
        result = client.search_many(["C", "CC", "CCC"], database="enamine-real")
        assert [item.status for item in result] == ["succeeded", "failed", "cancelled"]
        assert result.queries[1].error["code"] == "transport_error"
        assert result.queries[2].error["code"] == "site_unreachable"

        timed_out = client.search_many(["C", "CC"], database="enamine-real", timeout=0)
    assert len(seen) == 2  # nothing is sent once the site is unreachable or time is up
    assert timed_out.status == "failed"
    assert {item.error["code"] for item in timed_out} == {"client_timeout"}


def test_durable_only_arguments_are_rejected_distinctly(tmp_path):
    seen = []
    with Client(api_url=SITE, on_prem=True, transport=search_site(seen)) as client:
        for options in ({"state_file": tmp_path / "state.json"}, {"poll_interval": 2.0}):
            with pytest.raises(UnsupportedOnPremError) as caught:
                client.search_many(["CCO"], database="enamine-real", **options)
            assert caught.value.code == "unsupported_on_prem"
            assert caught.value.field == next(iter(options))
            assert isinstance(caught.value, DeepMedChemError)
        with pytest.raises(UnsupportedOnPremError, match="resume_search_many"):
            client.resume_search_many(tmp_path / "state.json")
    assert seen == [] and not (tmp_path / "state.json").exists()


def test_hosted_search_many_rejects_on_prem_only_concurrency(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(client_module, "resolve_api_key", lambda *a, **kw: "key")
    with Client(transport=httpx.MockTransport(lambda request: None)) as client:
        assert not client.is_on_prem
        with pytest.raises(ValueError, match="max_concurrency"):
            client.search_many(["CCO"], database="enamine-real", max_concurrency=2)
