import csv
import functools
import json

import httpx

import deepmedchem.cli as cli
from deepmedchem import Client

CATALOG = {
    "libraries": [
        {
            "database_id": "enamine-real-v5a",
            "name": "Enamine REAL v5a",
            "product_count": 357443832639,
            "capabilities": {
                "search": True,
                "search_cheese": ["shape", "esp"],
                "search_substructure": True,
                "sample": True,
            },
            "pricing": {
                "available": True,
                "currency": "USD",
                "default_amount_mg": 1,
                "ship_to": "US",
            },
        },
        {
            "database_id": "d2b-spacem1",
            "name": "D2B SpaceM1",
            "product_count": 1489758122,
            "capabilities": {"search": True, "search_cheese": [], "search_substructure": False},
            "pricing": {"available": False},
        },
    ]
}
USAGE = {
    "plan": "premium",
    "limit": 10000,
    "used": 1,
    "remaining": 9999,
    "resetAt": "2026-09-04T00:00:00+00:00",
    "secondsToReset": 3600,
    "unlimited": False,
    "promo": {"label": "10x September promo", "multiplier": 10.0, "baseLimit": 1000},
}
HITS = [
    {
        "rank": 1,
        "smiles": "O=C(O)Oc1ccccc1C(=O)O",
        "score": 0.7037,
        "price": 245,
        "product_id": "a",
    },
    {
        "rank": 2,
        "smiles": "COC(=O)Oc1ccccc1C(=O)O",
        "score": 0.6667,
        "price": None,
        "product_id": "b",
    },
]


def _handler(seen, request: httpx.Request) -> httpx.Response:
    seen.append(request)
    if request.url.path == "/api/v2/catalog":
        return httpx.Response(200, json=CATALOG)
    if request.url.path == "/rate-limit/status":
        return httpx.Response(200, json=USAGE)
    if request.url.path == "/api/v2/search":
        return httpx.Response(
            200,
            json={
                "results": HITS,
                "database_id": "enamine-real-v5a",
                "database_release": "2026-09-02.1",
                "scorer": "morgan",
                "metric": "ECFP4 Tanimoto",
                "timing_ms": {"total": 12.5},
                "warnings": [{"code": "truncated", "message": "Only two products."}],
            },
        )
    if request.url.path == "/api/v2/search_substructure":
        return httpx.Response(200, json={"results": HITS[:1], "database_id": "enamine-real-v5a"})
    if request.url.path == "/api/v2/sample":
        return httpx.Response(200, json={"results": HITS, "database_id": "enamine-real-v5a"})
    return httpx.Response(404, json={"error": {"code": "not_found", "message": "nope"}})


def _install_client(monkeypatch):
    seen = []
    monkeypatch.setattr(
        cli,
        "Client",
        functools.partial(
            Client,
            api_key="token",
            api_url="https://api.example.test",
            account_url="https://account.example.test",
            transport=httpx.MockTransport(functools.partial(_handler, seen)),
        ),
    )
    return seen


def test_databases_table_lists_size_pricing_and_order_email(monkeypatch, capsys) -> None:
    _install_client(monkeypatch)
    assert cli.main(["databases"]) == 0
    out = capsys.readouterr().out
    header = out.splitlines()[0].split()
    assert header == ["database", "molecules", "prices", "orders"]
    enamine = next(line for line in out.splitlines() if line.startswith("enamine-real-v5a"))
    assert enamine.split() == ["enamine-real-v5a", "357.4B", "yes", "info@enamine.net"]
    d2b = next(line for line in out.splitlines() if line.startswith("d2b-spacem1"))
    assert d2b.split() == ["d2b-spacem1", "1.5B", "-", "hello@molecule.one"]
    assert "2 databases, made on demand and delivered in 3-6 weeks." in out


def test_catalog_alias_prints_json(monkeypatch, capsys) -> None:
    _install_client(monkeypatch)
    assert cli.main(["catalog", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == CATALOG


def test_usage_reports_plan_credits_and_promo(monkeypatch, capsys) -> None:
    seen = _install_client(monkeypatch)
    assert cli.main(["usage"]) == 0
    out = capsys.readouterr().out
    assert "plan:      premium" in out
    assert "9,999 of 10,000 remaining today (1 used)" in out
    assert "10x September promo" in out
    assert seen[0].url.host == "account.example.test"
    assert seen[0].headers["x-api-key"] == "token"


def test_usage_json_uses_raw_payload(monkeypatch, capsys) -> None:
    _install_client(monkeypatch)
    assert cli.main(["usage", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan"] == "premium"
    assert payload["remaining"] == 9999


def test_search_prints_table_and_summary(monkeypatch, capsys) -> None:
    seen = _install_client(monkeypatch)
    assert cli.main(["search", "CC(=O)Oc1ccccc1C(=O)O", "-d", "enamine-real-v5a", "-n", "2"]) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0].split() == ["rank", "score", "price", "smiles"]
    assert "   1  0.7037   $245  O=C(O)Oc1ccccc1C(=O)O" in lines
    assert "   2  0.6667      -  COC(=O)Oc1ccccc1C(=O)O" in lines
    assert "Searched 357.4B molecules (Enamine REAL v5a) in 12 ms." in lines
    assert "Similarity range: 0.67-0.70 ECFP4 Tanimoto." in lines
    assert "product_id" not in captured.out
    assert captured.err == ""
    # The search request goes out before the catalog lookup used for the summary line.
    assert [request.url.path for request in seen] == ["/api/v2/search", "/api/v2/catalog"]
    body = json.loads(seen[0].content)
    assert body == {
        "query_smiles": "CC(=O)Oc1ccccc1C(=O)O",
        "database_id": "enamine-real-v5a",
        "limit": 2,
        "include_synthons": False,
    }


def test_search_saves_csv_with_prices(monkeypatch, capsys, tmp_path) -> None:
    _install_client(monkeypatch)
    target = tmp_path / "hits.csv"
    assert cli.main(["search", "CCO", "-d", "enamine-real-v5a", "-o", str(target)]) == 0
    assert f"Saved 2 molecules to {target} (csv)." in capsys.readouterr().out
    with open(target, newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["smiles"] for row in rows] == [hit["smiles"] for hit in HITS]
    assert rows[0]["price"] == "245"
    assert rows[1]["price"] == ""
    assert list(rows[0])[:4] == ["rank", "smiles", "score", "price"]


def test_search_output_format_can_be_forced(monkeypatch, capsys, tmp_path) -> None:
    _install_client(monkeypatch)
    target = tmp_path / "hits.dat"
    assert (
        cli.main(["search", "CCO", "-d", "enamine-real-v5a", "-o", str(target), "--format", "smi"])
        == 0
    )
    assert target.read_text().splitlines() == [
        "O=C(O)Oc1ccccc1C(=O)O a",
        "COC(=O)Oc1ccccc1C(=O)O b",
    ]


def test_search_unknown_suffix_is_reported(monkeypatch, capsys, tmp_path) -> None:
    _install_client(monkeypatch)
    assert cli.main(["search", "CCO", "-d", "db", "-o", str(tmp_path / "hits.xyz")]) == 1
    assert "Cannot infer an output format" in capsys.readouterr().err


def test_substructure_and_sample_reach_their_operations(monkeypatch, capsys) -> None:
    seen = _install_client(monkeypatch)
    assert cli.main(["substructure", "c1ccccc1", "-d", "db", "-f", "smiles", "-n", "5"]) == 0
    assert cli.main(["sample", "-d", "db", "-n", "7", "--seed", "3"]) == 0
    paths = [request.url.path for request in seen if request.url.path != "/api/v2/catalog"]
    assert paths == ["/api/v2/search_substructure", "/api/v2/sample"]
    assert json.loads(seen[0].content)["query"] == {"format": "smiles", "value": "c1ccccc1"}
    sample_request = next(request for request in seen if request.url.path == "/api/v2/sample")
    assert json.loads(sample_request.content) == {
        "database_id": "db",
        "count": 7,
        "include_synthons": False,
        "seed": 3,
    }
    out = capsys.readouterr().out
    assert "rank  match  price  smiles" in out
    assert "   1  exact   $245  O=C(O)Oc1ccccc1C(=O)O" in out
    assert "1 exact substructure matches returned." in out
    assert "rank  price  smiles" in out
    assert "Random sample of 2 from 357.4B molecules (Enamine REAL v5a)." in out


def test_api_errors_exit_nonzero_with_code(monkeypatch, capsys) -> None:
    def handler(request):
        return httpx.Response(
            429, json={"error": {"code": "credit_limit_exceeded", "message": "No credits."}}
        )

    monkeypatch.setattr(
        cli,
        "Client",
        functools.partial(
            Client,
            api_key="token",
            api_url="https://api.example.test",
            transport=httpx.MockTransport(handler),
            max_retries=0,
        ),
    )
    assert cli.main(["search", "CCO", "-d", "db"]) == 1
    assert "No credits. [credit_limit_exceeded]" in capsys.readouterr().err
