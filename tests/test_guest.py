"""Guest requests share the real SDK transport, models, facades and CLI."""

import asyncio
import json

import httpx
import pytest

import deepmedchem as dmc
import deepmedchem.cli as cli
import deepmedchem.client as client_module


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    monkeypatch.setattr(client_module, "resolve_api_key", lambda *a, **kw: None)


def guest_response(request):
    assert "x-api-key" not in request.headers
    assert request.headers["x-dmc-client"] == "deepmedchem-python"
    body = json.loads(request.content or b"{}")
    return httpx.Response(
        200,
        json={
            "mode": "guest",
            "database_id": body.get("database_id"),
            "results": [{"smiles": "CCO", "rank": 1}],
            "warnings": [
                {"code": "guest_limit_clamped", "message": "Guests receive at most 20 results"}
            ],
        },
    )


@pytest.mark.parametrize("asynchronous", [False, True])
def test_guest_operations_and_result_metadata(asynchronous):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return guest_response(request)

    options = {"transport": httpx.MockTransport(handler)}
    if asynchronous:

        async def run():
            async with dmc.AsyncClient(**options) as client:
                assert client.is_guest
                assert (await client.catalog())["mode"] == "guest"
                return [
                    await client.search("CCO", database="enamine", method=method, limit=100)
                    for method in ("morgan", "shape", "esp")
                ] + [await client.sample(database="enamine", count=100)]

        results = asyncio.run(run())
    else:
        with dmc.Client(**options) as client:
            assert client.is_guest
            assert client.catalog()["mode"] == "guest"
            results = [
                client.search("CCO", database="enamine", method=method, limit=100)
                for method in ("morgan", "shape", "esp")
            ]
            results.append(client.sample(database="enamine", count=100))
    assert seen == [
        "/api/v2/catalog",
        "/api/v2/search",
        "/api/v2/search_cheese",
        "/api/v2/search_cheese",
        "/api/v2/sample",
    ]
    for result in results:
        assert result.mode == result.raw["mode"] == "guest"
        assert result.warnings[0].code == "guest_limit_clamped"
        assert list(result) == ["CCO"]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_explicit_guest_bypasses_saved_environment_and_provider_lookup(monkeypatch, asynchronous):
    def unexpected(*args, **kwargs):
        raise AssertionError("guest=True must not look up any credentials")

    monkeypatch.setattr(client_module, "resolve_api_key", unexpected)
    options = {"guest": True, "transport": httpx.MockTransport(guest_response)}
    if asynchronous:
        assert asyncio.run(dmc.aio.search("CCO", database="enamine", **options)).mode == "guest"
        assert asyncio.run(dmc.aio.sample(database="enamine", **options)).mode == "guest"
        assert asyncio.run(dmc.aio.catalog(**options))["mode"] == "guest"
    else:
        assert dmc.search("CCO", database="enamine", **options).mode == "guest"
        assert dmc.sample(database="enamine", **options).mode == "guest"
        assert dmc.catalog(**options)["mode"] == "guest"


@pytest.mark.parametrize("client_class", [dmc.Client, dmc.AsyncClient])
@pytest.mark.parametrize(
    "credentials", [{"api_key": "explicit"}, {"credential_provider": lambda: "x"}]
)
def test_guest_rejects_conflicting_explicit_credentials(client_class, credentials):
    with pytest.raises(ValueError, match="guest=True cannot be combined"):
        client_class(guest=True, **credentials)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "operation",
    [
        lambda c: c.usage(),
        lambda c: c.search_substructure("C", database="enamine"),
        lambda c: c.search("CCO", database="auto"),
        lambda c: c.search("CCO", database="enamine", require=dmc.RequireStructure(smarts="C")),
        lambda c: c.sample(database="enamine", where="mw<400"),
        lambda c: c.optimizations.list(),
    ],
)
def test_account_operations_fail_before_network(asynchronous, operation):
    def unexpected(request):
        raise AssertionError("account-only request sent as guest")

    options = {"transport": httpx.MockTransport(unexpected)}
    if asynchronous:

        async def run():
            async with dmc.AsyncClient(**options) as client:
                await operation(client)

        def call():
            return asyncio.run(run())
    else:
        with dmc.Client(**options) as client:
            with pytest.raises(dmc.DeepMedChemError, match="requires an API key") as error:
                operation(client)
        assert error.value.code == "missing_api_key"
        return
    with pytest.raises(dmc.DeepMedChemError, match="requires an API key") as error:
        call()
    assert error.value.code == "missing_api_key"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("code,status", [("unauthorized", 401), ("guest_limit", 429)])
def test_guest_errors_preserve_details_and_do_not_retry(asynchronous, code, status):
    seen = []
    details = {"login_url": "https://cheese.deepmedchem.com/", "retry_after": 3600}

    def handler(request):
        seen.append(request)
        return httpx.Response(
            status,
            headers={"Retry-After": "3600"},
            json={
                "error": {
                    "code": code,
                    "message": "Sign in or retry later",
                    "details": details,
                }
            },
        )

    options = {"transport": httpx.MockTransport(handler)}
    with pytest.raises(dmc.DeepMedChemError) as error:
        if asynchronous:
            asyncio.run(dmc.aio.catalog(**options))
        else:
            dmc.catalog(**options)
    assert len(seen) == 1
    assert error.value.code == code
    assert error.value.status_code == status
    assert error.value.details == details


@pytest.mark.parametrize("asynchronous", [False, True])
def test_configured_invalid_key_is_not_downgraded_to_guest(monkeypatch, asynchronous):
    monkeypatch.setattr(client_module, "resolve_api_key", lambda *a, **kw: "invalid-key")
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["x-api-key"] == "invalid-key"
        return httpx.Response(401, json={"error": {"code": "unauthorized"}})

    with pytest.raises(dmc.DeepMedChemError):
        if asynchronous:
            asyncio.run(dmc.aio.catalog(transport=httpx.MockTransport(handler)))
        else:
            dmc.catalog(transport=httpx.MockTransport(handler))
    assert len(seen) == 1


def test_guest_cli_forwards_flag_and_prints_limits(monkeypatch, capsys):
    real_client = dmc.Client

    def client(**kwargs):
        assert kwargs["guest"] is True
        return real_client(**kwargs, transport=httpx.MockTransport(guest_response))

    monkeypatch.setattr(cli, "Client", client)
    assert cli.main(["search", "CCO", "-d", "enamine", "--guest", "-n", "100"]) == 0
    captured = capsys.readouterr()
    assert "Guest" in captured.err
    assert "at most 20" in captured.err


def test_guest_cli_json_preserves_metadata(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "Client", lambda **kw: dmc.Client(**kw, transport=httpx.MockTransport(guest_response))
    )
    assert cli.main(["sample", "-d", "enamine", "--guest", "--json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["mode"] == "guest"
    assert payload["warnings"][0]["code"] == "guest_limit_clamped"
    assert not captured.err
