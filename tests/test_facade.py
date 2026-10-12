import asyncio
import json

import httpx

import deepmedchem as dmc


def _handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content or b"{}")
    if request.url.path == "/api/v2/catalog":
        return httpx.Response(200, json={"spaces": []})
    return httpx.Response(
        200,
        json={
            "database_id": body["database_id"],
            "scorer": body.get("scorer", "morgan"),
            "results": [
                {"rank": 1, "smiles": "CCO", "score": 1.0, "price": 163}
            ],
        },
    )


def test_module_level_operations_use_scientific_surface() -> None:
    options = {
        "api_key": "token",
        "api_url": "https://example.test",
        "transport": httpx.MockTransport(_handler),
    }
    result = dmc.search("CCO", database="db", method="shape", **options)
    assert list(result) == ["CCO"]
    assert result.method == "shape"
    assert result.prices == [163]
    assert dmc.catalog(**options) == {"spaces": []}


def test_prices_are_read_from_the_search_response_without_an_extra_request() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _handler(request)

    result = dmc.search(
        "CCO",
        database="db",
        api_key="token",
        api_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )

    assert result.prices == [163]
    assert result.hits[0].price == 163
    assert result.prices == [163]
    assert len(requests) == 1


def test_async_namespace_matches_sync_semantics() -> None:
    async def scenario():
        result = await dmc.aio.search(
            "CCO",
            database="db",
            api_key="token",
            api_url="https://example.test",
            transport=httpx.MockTransport(_handler),
        )
        assert result[:] == ["CCO"]

    asyncio.run(scenario())
