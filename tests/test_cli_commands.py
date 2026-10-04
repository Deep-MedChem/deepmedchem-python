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
    assert header == ["abbreviation", "molecules", "prices", "orders"]
    enamine = next(line for line in out.splitlines() if line.startswith("enamine"))
    assert enamine.split() == ["enamine", "357.4B", "yes", "info@enamine.net"]
    d2b = next(line for line in out.splitlines() if line.startswith("spacem1"))
    assert d2b.split() == ["spacem1", "1.5B", "-", "hello@molecule.one"]
    assert "2 databases. Lead times vary by database; see --detailed." in out


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
    assert [request.url.path for request in seen] == [
        "/api/v2/search",
        "/api/v2/catalog",
        "/available_databases_full",
    ]
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
    catalog_paths = {"/api/v2/catalog", "/available_databases_full"}
    paths = [request.url.path for request in seen if request.url.path not in catalog_paths]
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


def test_detailed_catalog_uses_live_counts_and_preserves_raw_json(monkeypatch, capsys):
    _install_client(monkeypatch)
    assert cli.main(["databases", "--detailed"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == [
        "abbreviation", "database_id", "biosolveit_mapping", "type", "molecules",
        "availability", "success", "prices", "orders", "link",
    ]
    assert "enamine-real-v5a" in out
    assert "REALSpace_95bn_2026-04**" in out
    assert "357.4B" in out  # Live fixture, not the README snapshot's 336.7B.
    assert "public Enamine building blocks" in out
    assert "MCULE-FULL" not in out
    assert cli.main(["catalog", "--detailed", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == CATALOG


def test_unknown_catalog_database_is_still_displayed(capsys):
    cli._print_database_table({"libraries": [{"database_id": "private-space"}]}, detailed=True)
    out = capsys.readouterr().out
    assert "private-space" in out


def test_cli_search_accepts_abbreviation(monkeypatch, capsys):
    seen = _install_client(monkeypatch)
    assert cli.main(["search", "CCO", "-d", "enamine"]) == 0
    search = next(r for r in seen if r.url.path == "/api/v2/search")
    assert json.loads(search.content)["database_id"] == "enamine-real-v5a"


def test_catalog_display_order_and_unavailable_enamine_prices(capsys):
    ids = [
        "private-space", "d2b-spacem1", "cheminfinita-2026-02", "vast-2026-h2",
        "synple-synple-2025-10", "synple-explore-2025-10", "freedom-space-5",
        "enamine-real-v5a",
    ]
    catalog = {"libraries": [
        {"database_id": db, "product_count": 1000, "pricing": {"available": False}}
        for db in ids
    ]}
    original = json.dumps(catalog)
    expected = [
        "enamine", "freedom", "explore", "synple", "vast", "cheminfinita", "spacem1",
        "private-space",
    ]
    for detailed in (False, True):
        cli._print_database_table(catalog, detailed=detailed)
        lines = capsys.readouterr().out.splitlines()[2:10]
        assert [line.split()[0] for line in lines] == expected
        if not detailed:
            assert lines[0].split() == ["enamine", "1.0K", "-", "info@enamine.net"]
    assert json.dumps(catalog) == original


def test_databases_table_includes_classic_cheese_catalogues(capsys):
    catalog = {"libraries": [
        {"database_id": "enamine-real-v5a", "product_count": 1000, "pricing": {"available": True}},
        {"database_id": "MOLPORT", "served_by": "cheese", "product_count": 5900000,
         "contact_email": "sales@molport.com", "pricing": {"available": False}},
    ]}
    cli._print_database_table(catalog, detailed=False)
    lines = capsys.readouterr().out.splitlines()
    molport = next(line for line in lines if line.startswith("molport"))
    assert molport.split() == ["molport", "5.9M", "-", "sales@molport.com"]
    enamine_index = next(i for i, line in enumerate(lines) if line.startswith("enamine"))
    assert lines.index(molport) > enamine_index


# Tests for classic (enumerated and in-stock) libraries served by CHEESE Search


def _classic_search_handler(request: httpx.Request) -> httpx.Response:
    """Mock handler for classic database searches via CHEESE Search."""
    if request.url.path == "/api/v2/catalog":
        return httpx.Response(200, json=CATALOG)
    if request.url.path == "/rate-limit/status":
        return httpx.Response(200, json=USAGE)
    if request.url.path == "/available_databases_full":
        # CHEESE Search catalog
        return httpx.Response(
            200,
            json={
                "molport": {"Vendor": "Molport", "Number of molecules": "5900000", "Website": "molport.com", "Email": "sales@molport.com"},
                "mcule-in-stock": {"Vendor": "MCULE", "Number of molecules": "7200000"},
                "enamine-real": {"Vendor": "Enamine", "Number of molecules": "9560000000"},
                "zinc15": {"Vendor": "ZINC15", "Number of molecules": "697100000"},
            },
        )
    if request.url.path == "/molsearch":
        # CHEESE Search similarity search endpoint (used for classic libraries)
        return httpx.Response(
            200,
            json={
                "neighbors": [
                    {"smiles": "O=C(O)Oc1ccccc1C(=O)O", "id": "mol-001", "similarity": 1.0},
                    {"smiles": "COC(=O)Oc1ccccc1C(=O)O", "id": "mol-002", "similarity": 0.95},
                ],
                "search_info": {"search_id": "req-001"},
            },
        )
    return httpx.Response(404, json={"error": {"code": "not_found", "message": "nope"}})


def test_cli_search_works_with_classic_instock_library(monkeypatch, capsys):
    """Test that CLI search works with classic in-stock libraries (molport, mcule, etc)."""
    seen = []

    def handler(request):
        seen.append(request)
        return _classic_search_handler(request)

    monkeypatch.setattr(
        cli,
        "Client",
        functools.partial(
            Client,
            api_key="token",
            api_url="https://api.example.test",
            account_url="https://account.example.test",
            transport=httpx.MockTransport(handler),
        ),
    )
    assert cli.main(["search", "CCO", "-d", "molport", "-n", "2"]) == 0
    captured = capsys.readouterr()
    # CHEESE Search results have similarity scores but no price estimates
    assert "rank" in captured.out and "score" in captured.out and "price" in captured.out
    assert "O=C(O)Oc1ccccc1C(=O)O" in captured.out
    assert "MOLPORT" in captured.out


def test_cli_search_works_with_classic_enumerated_library(monkeypatch, capsys):
    """Test that CLI search works with enumerated libraries (enamine-real, zinc15, etc)."""
    seen = []

    def handler(request):
        seen.append(request)
        return _classic_search_handler(request)

    monkeypatch.setattr(
        cli,
        "Client",
        functools.partial(
            Client,
            api_key="token",
            api_url="https://api.example.test",
            account_url="https://account.example.test",
            transport=httpx.MockTransport(handler),
        ),
    )
    assert cli.main(["search", "CCO", "-d", "enamine-real", "-n", "2"]) == 0
    captured = capsys.readouterr()
    assert "rank" in captured.out and "score" in captured.out
    assert "O=C(O)Oc1ccccc1C(=O)O" in captured.out
    assert "ENAMINE-REAL" in captured.out


def test_cli_search_classic_library_accepts_alias(monkeypatch, capsys):
    """Test that classic library aliases resolve correctly (e.g., molport → MOLPORT)."""
    seen = []

    def handler(request):
        seen.append(request)
        return _classic_search_handler(request)

    monkeypatch.setattr(
        cli,
        "Client",
        functools.partial(
            Client,
            api_key="token",
            api_url="https://api.example.test",
            account_url="https://account.example.test",
            transport=httpx.MockTransport(handler),
        ),
    )
    assert cli.main(["search", "CCO", "-d", "molport"]) == 0
    # Verify that the alias was resolved to the uppercase ID
    classic_search_request = next((r for r in seen if r.url.path == "/molsearch"), None)
    assert classic_search_request is not None
    assert "db_names=MOLPORT" in str(classic_search_request.url.params)


def test_cli_search_classic_library_output_formats(monkeypatch, tmp_path):
    """Test that classic library search results can be exported to various formats."""
    def handler(request):
        return _classic_search_handler(request)

    monkeypatch.setattr(
        cli,
        "Client",
        functools.partial(
            Client,
            api_key="token",
            api_url="https://api.example.test",
            account_url="https://account.example.test",
            transport=httpx.MockTransport(handler),
        ),
    )
    # Test CSV export
    csv_file = tmp_path / "classic_results.csv"
    assert cli.main(["search", "CCO", "-d", "molport", "-o", str(csv_file)]) == 0
    assert csv_file.exists()
    with open(csv_file, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["smiles"] == "O=C(O)Oc1ccccc1C(=O)O"

    # Test JSON export
    json_file = tmp_path / "classic_results.json"
    assert cli.main(["search", "CCO", "-d", "mcule-in-stock", "-o", str(json_file), "--format", "json"]) == 0
    assert json_file.exists()
    result = json.loads(json_file.read_text())
    assert "results" in result


def test_cli_substructure_unsupported_on_classic_libraries(monkeypatch, capsys):
    """Test that substructure search raises unsupported_operation for classic libraries."""
    def handler(request):
        if request.url.path == "/api/v2/catalog":
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(404)

    monkeypatch.setattr(
        cli,
        "Client",
        functools.partial(
            Client,
            api_key="token",
            api_url="https://api.example.test",
            account_url="https://account.example.test",
            transport=httpx.MockTransport(handler),
            max_retries=0,
        ),
    )
    assert cli.main(["substructure", "c1ccccc1", "-d", "molport"]) == 1
    captured = capsys.readouterr()
    assert "unsupported_operation" in captured.err or "not available" in captured.err


def test_cli_sample_unsupported_on_classic_libraries(monkeypatch, capsys):
    """Test that sampling raises unsupported_operation for classic libraries."""
    def handler(request):
        if request.url.path == "/api/v2/catalog":
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(404)

    monkeypatch.setattr(
        cli,
        "Client",
        functools.partial(
            Client,
            api_key="token",
            api_url="https://api.example.test",
            account_url="https://account.example.test",
            transport=httpx.MockTransport(handler),
            max_retries=0,
        ),
    )
    assert cli.main(["sample", "-d", "enamine-real", "-n", "10"]) == 1
    captured = capsys.readouterr()
    assert "unsupported_operation" in captured.err or "not available" in captured.err
