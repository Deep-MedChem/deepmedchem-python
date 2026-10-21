"""The backend's identity tie-break must survive both public client paths."""
import asyncio
import json

import httpx

from deepmedchem import AsyncClient, Client


def test_exact_identity_order_survives_equal_scores_sync_and_async():
    # Deliberately opposite lexical/product-ID order; score alone cannot choose.
    hits = [
        {"smiles": "C[C@H](O)CC", "product_id": "z-exact", "score": 1.0},
        {"smiles": "C[C@@H](O)CC", "product_id": "a-stereoisomer", "score": 1.0},
    ]

    def handler(request):
        assert request.url.path == "/api/v2/search"
        payload = json.loads(request.content)
        assert payload["query_smiles"] == hits[0]["smiles"]
        # Recovery is a server default; callers need no new request flag.
        assert "identity_index" not in payload
        return httpx.Response(200, json={"results": hits})

    kwargs = dict(api_key="test", api_url="https://example.test",
                  transport=httpx.MockTransport(handler))
    with Client(**kwargs) as client:
        assert client.search(hits[0]["smiles"], database="enamine-real-v5a").results == hits

    async def run():
        async with AsyncClient(**kwargs) as client:
            result = await client.search(hits[0]["smiles"], database="enamine-real-v5a")
            assert result.results == hits

    asyncio.run(run())
