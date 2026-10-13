import asyncio

import httpx
import pytest
from test_subsets import QUERY, _base, _install_client, _sent, _transport

import deepmedchem
import deepmedchem.cli as cli
from deepmedchem import AsyncClient, Client, Selection, SelectionResult
from deepmedchem.subsets import Filters

PRICE = {"max": 200, "currency": "USD", "amount_mg": 1, "ship_to": "US", "missing": "reject"}


def _priced(database="enamine-real-v5a", **price):
    return {**_base(database), "constraints": {"price": {**PRICE, **price}}}


def test_cli_max_price_sends_one_priced_similarity_selection(monkeypatch, capsys):
    seen = _install_client(monkeypatch)
    assert cli.main(["search", QUERY, "-d", "enamine", "--max-price", "200"]) == 0
    assert _sent(seen) == _priced()
    assert not [r for r in seen if r.url.path == "/api/v2/search"]
    out = capsys.readouterr().out
    assert "price <= $200 (1 mg, US delivery); unpriced products excluded" in out


def test_cli_keep_unpriced_and_dollar_sign(monkeypatch):
    seen = _install_client(monkeypatch)
    argv = ["search", QUERY, "-d", "enamine", "--max-price", "$199.5", "--keep-unpriced"]
    assert cli.main(argv) == 0
    assert _sent(seen) == _priced(max=199.5, missing="keep")


def test_cli_price_combines_with_other_filters(monkeypatch):
    seen = _install_client(monkeypatch)
    argv = ["search", QUERY, "-d", "enamine:ro5", "--max-price", "200", "--where", "mw<=450"]
    assert cli.main(argv) == 0
    constraints = _sent(seen)["constraints"]
    assert constraints["price"] == PRICE
    assert constraints["presets"] == [{"preset_id": "lipinski-ro5", "version": "1"}]
    assert constraints["properties"][0]["property_id"] == "rdkit.mol_wt"


def test_cli_sample_with_max_price(monkeypatch):
    seen = _install_client(monkeypatch)
    argv = ["sample", "-d", "enamine", "-n", "5", "--seed", "7", "--max-price", "200"]
    assert cli.main(argv) == 0
    assert _sent(seen) == {
        "schema_version": "molecule-selection/1",
        "database": {"database_id": "enamine-real-v5a"},
        "references": [],
        "strategy": {"type": "sample", "distribution": "route_product_tuple"},
        "constraints": {"price": PRICE},
        "objectives": [],
        "portfolio": {"limit": 5},
        "execution": {"quality": "balanced", "seed": 7},
        "include": [],
    }


@pytest.mark.parametrize(
    "argv, message",
    [
        (["--max-price", "-1"], "non-negative"),
        (["--max-price", "cheap"], "number of USD"),
        (["--max-price", "inf"], "non-negative"),
        (["--keep-unpriced"], "keep_unpriced needs max_price"),
    ],
)
def test_invalid_price_fails_before_any_request(monkeypatch, capsys, argv, message):
    seen = _install_client(monkeypatch)
    assert cli.main(["search", QUERY, "-d", "enamine", *argv]) == 1
    assert message in capsys.readouterr().err
    assert seen == []


def test_python_search_with_max_price_returns_a_selection_result():
    seen = []
    with Client(
        api_key="token", api_url="https://api.example.test", transport=_transport(seen)
    ) as client:
        result = client.search(QUERY, database="enamine", max_price=200)
    assert isinstance(result, SelectionResult)
    assert _sent(seen) == _priced()


def test_module_facade_passes_max_price():
    seen = []
    deepmedchem.search(
        QUERY,
        database="enamine",
        max_price=200,
        keep_unpriced=True,
        api_key="token",
        api_url="https://api.example.test",
        transport=_transport(seen),
    )
    assert _sent(seen) == _priced(missing="keep")


def test_async_sample_with_max_price():
    seen = []

    async def run():
        async with AsyncClient(
            api_key="token", api_url="https://api.example.test", transport=_transport(seen)
        ) as client:
            return await client.sample(database="enamine", count=3, max_price=200)

    asyncio.run(run())
    body = _sent(seen)
    assert body["strategy"]["type"] == "sample"
    assert body["constraints"] == {"price": PRICE}


def test_selection_builder_and_filters():
    selection = Selection.from_database("enamine").sample().limit(5).max_price(200)
    assert selection.to_dict()["constraints"]["price"] == PRICE
    assert Filters.build(max_price="250")
    assert not Filters.build()
    with pytest.raises(ValueError):
        Selection.from_database("enamine").max_price(float("nan"))


def test_missing_pricing_capability_is_reported_on_the_price_field(monkeypatch, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "capability_unavailable",
                    "message": "The selected database has no prices.",
                    "field": "constraints.price",
                    "details": {"capability": "pricing"},
                }
            },
        )

    with Client(
        api_key="token", api_url="https://api.example.test", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(deepmedchem.DeepMedChemError) as error:
            client.search(QUERY, database="enamine", max_price=200)
    assert "no prices" in str(error.value)
    # A missing price capability is not mistaken for a retired classic database.
    assert "classic CHEESE" not in str(error.value)
