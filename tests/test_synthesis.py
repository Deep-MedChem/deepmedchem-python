import asyncio
import csv
import json

import httpx
import pytest

import deepmedchem as dmc
import deepmedchem.cli as cli
from deepmedchem.auto import synthesis_days

ESTIMATE = {
    "min": 5,
    "max": 7,
    "unit": "days",
    "day_basis": "business",
    "source": "Onepot supplier declaration",
    "as_of": "2026-10-21",
}
SMILES = "CC/C(=C(\\c1ccccc1)c1ccc(OCCN(C)C(=O)NC)cc1)c1ccccc1"


def install(monkeypatch):
    seen = []

    def handle(request):
        body = json.loads(request.content or "{}")
        seen.append((request.url.path, body))
        plan = {
            "selected": [{"database_id": "onepot-core-2-lite", "synthesis": ESTIMATE}],
            "excluded": [{"database_id": "enamine-real-v5a", "reason": "synthesis_time_unknown"}],
            "sources": [{"database_id": "onepot-core-2-lite", "status": "succeeded"}],
            "max_synthesis_days": body.get("max_synthesis_days"),
        }
        if request.url.path.endswith(":plan"):
            return httpx.Response(200, json=plan)
        return httpx.Response(
            200,
            json={
                "database_id": "auto",
                "scorer": "morgan",
                "metric": "ECFP4 Tanimoto",
                "results": [
                    {
                        "rank": 1,
                        "smiles": SMILES,
                        "score": 0.6957,
                        "price": 250,
                        "database_id": "onepot-core-2-lite",
                        "synthesis": ESTIMATE,
                        "availability": "make_on_demand",
                    }
                ],
                "routing": plan,
                "all_sources_searched": True,
            },
        )

    connection = {"api_key": "test", "transport": httpx.MockTransport(handle)}
    monkeypatch.setattr(cli, "Client", lambda **kw: dmc.Client(**connection, **kw))
    return connection, seen


@pytest.mark.parametrize("value", ["7d", "7 days", "7day", " 7 D ", 7])
def test_duration(value):
    assert synthesis_days(value) == 7


@pytest.mark.parametrize("value", ["7m", "0d", "-2d", "7.5d", "seven", 7.5, True, 3651])
def test_bad_duration(value):
    with pytest.raises(ValueError):
        synthesis_days(value)


def test_default_auto_and_filter_parity(monkeypatch):
    connection, seen = install(monkeypatch)
    options = dict(synthesis="7d", include_catalogues=True, max_price=300)
    dmc.search("CCO", **connection, **options)
    with dmc.Client(**connection) as client:
        client.search("CCO", **options)
        client.plan_search("CCO", **options)

    async def run():
        await dmc.aio.search("CCO", **connection, **options)
        async with dmc.AsyncClient(**connection) as client:
            await client.search("CCO", **options)
            await client.plan_search("CCO", **options)

    asyncio.run(run())
    for _, body in seen:
        assert body["max_synthesis_days"] == 7
        assert body["include_catalogues"] is True
        assert body["scope"] == "orderable"
        assert body["selection"]["constraints"]["price"]["max"] == 300
        assert body["selection"]["database"]["database_id"] == "auto"


def test_explicit_source_with_timing_uses_server_validation(monkeypatch):
    connection, seen = install(monkeypatch)
    dmc.search("CCO", database="onepot", synthesis=7, **connection)
    asyncio.run(dmc.aio.search("CCO", database="onepot", synthesis=7, **connection))
    assert seen[0] == seen[1]
    assert seen[0][1]["databases"] == ["onepot-core-2-lite"]
    assert seen[0][1]["include_catalogues"] is True


def test_substructure_synthesis_parity(monkeypatch):
    connection, seen = install(monkeypatch)
    dmc.substructure("CO", database="auto", synthesis=7, include_catalogues=True, **connection)
    asyncio.run(
        dmc.aio.substructure(
            "CO", database="auto", synthesis=7, include_catalogues=True, **connection
        )
    )
    assert seen[0] == seen[1]
    assert seen[0][1]["max_synthesis_days"] == 7


def test_cli_auto_table_full_smiles_and_explicit_units(monkeypatch, capsys):
    _, seen = install(monkeypatch)
    assert cli.main(["search", SMILES, "--synthesis", "7d", "-n", "5"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == [
        "rank",
        "database",
        "score",
        "price",
        "synthesis",
        "smiles",
    ]
    assert SMILES in out
    assert "5–7 days" in out and "onepot" in out
    assert "..." not in out and " bd" not in out
    assert "business days" in out and "Shipping is additional" in out
    assert len(seen) == 1  # no catalogue request for an 'auto' pseudo-library
    assert seen[0][1]["max_synthesis_days"] == 7
    assert seen[0][1]["include_catalogues"] is False


def test_cli_all_and_filters_do_not_bypass_auto(monkeypatch, capsys):
    _, seen = install(monkeypatch)
    assert (
        cli.main(
            [
                "search",
                "CCO",
                "--all",
                "--max-price",
                "300",
                "--where",
                "mw<400",
                "--require-count",
                "2-",
                "C",
            ]
        )
        == 0
    )
    capsys.readouterr()
    body = seen[0][1]
    assert body["include_catalogues"] is True and body["scope"] == "orderable"
    constraints = body["selection"]["constraints"]
    assert constraints["price"]["max"] == 300
    assert constraints["properties"][0]["value"] == 400
    assert constraints["structures"]["all"][0]["count"]["min"] == 2


def test_cli_explain_is_plan_only(monkeypatch, capsys):
    _, seen = install(monkeypatch)
    assert cli.main(["search", "CCO", "--synthesis", "7d", "--explain"]) == 0
    assert [path for path, _ in seen] == ["/api/v2/search_auto:plan"]
    out = capsys.readouterr().out
    assert "synthesis_time_unknown" in out
    assert "no search credit" in out


def test_cli_rejects_all_with_explicit_source_before_network(monkeypatch, capsys):
    _, seen = install(monkeypatch)
    assert cli.main(["search", "CCO", "-d", "onepot", "--all"]) == 1
    assert not seen
    assert "automatic database selection" in capsys.readouterr().err


def test_native_response_and_csv_preserve_estimate(monkeypatch, tmp_path):
    connection, _ = install(monkeypatch)
    result = dmc.search("CCO", synthesis=7, **connection)
    assert result.hits[0].synthesis.max == 7
    assert result.hits[0].synthesis.label == "5–7 days"
    path = tmp_path / "demo.csv"
    result.to_csv(path)
    row = next(csv.DictReader(path.open()))
    assert json.loads(row["synthesis"]) == ESTIMATE
    assert row["smiles"] == SMILES
    assert row["database_id"] == "onepot-core-2-lite"


def test_months_calendar_days_stock_unknown_and_no_offer_mixing(capsys):
    result = dmc.SearchResult(
        database_id="auto",
        scorer="morgan",
        results=[
            {
                "smiles": "C",
                "database_id": "vendor-a",
                "synthesis": {**ESTIMATE, "min": 1, "max": 2, "unit": "months"},
                "sources": [{"database_id": "vendor-b", "synthesis": ESTIMATE}],
            },
            {"smiles": "CC", "availability": "in_stock"},
            {"smiles": "CCC"},
            {"smiles": "CO", "synthesis": {**ESTIMATE, "day_basis": "calendar"}},
        ],
    )
    cli._print_result_table(result)
    out = capsys.readouterr().out
    assert "1–2 months" in out and "in stock" in out and "unknown" in out
    assert "5–7 calendar days" in out
    assert "5–7 days" not in out  # never borrow alternative vendor timing
