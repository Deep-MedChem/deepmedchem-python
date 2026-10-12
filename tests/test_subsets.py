import asyncio
import functools
import json

import httpx
import pytest

import deepmedchem
import deepmedchem.cli as cli
from deepmedchem import AsyncClient, Client, Selection, SelectionResult
from deepmedchem.subsets import (
    ALPHA_AMINO_ACID,
    SUBSETS,
    Filters,
    PropertyCondition,
    get_subset,
    parse_condition,
    split_database,
)

QUERY = "CC(=O)Oc1ccccc1C(=O)O"
HITS = [
    {"rank": 1, "smiles": "CC(=O)N[C@@H](C)C(=O)O", "score": 0.41, "price": 120},
    {"rank": 2, "smiles": "CC(=O)NCC(=O)OC", "score": 0.38, "price": None},
]


def _selection_response(document, *, warnings=()):
    return {
        "id": "sel_0123",
        "object": "selection",
        "status": "completed_with_warnings" if warnings else "completed",
        "selection_hash": "hash",
        "normalized_selection": document,
        "database_release": "2026-10.1",
        "database_id": document["database"]["database_id"],
        "results": HITS,
        "counts": {"returned": len(HITS)},
        "timing_ms": {"total": 41.0},
        "warnings": list(warnings),
    }


def _handler(seen, warnings, request: httpx.Request) -> httpx.Response:
    seen.append(request)
    body = json.loads(request.content) if request.content else None
    if request.url.path == "/api/v2/catalog":
        return httpx.Response(200, json={"libraries": []})
    if request.url.path == "/api/v2/selections":
        return httpx.Response(200, json=_selection_response(body, warnings=warnings))
    if request.url.path == "/api/v2/selections:validate":
        return httpx.Response(
            200,
            json={
                "valid": True,
                "normalized_selection": body,
                "selection_hash": "hash",
                "constraint_execution": [
                    {
                        "path": "constraints.structures.all[0]",
                        "status": "conservative_approximate_pushdown",
                    }
                ],
                "warnings": [],
            },
        )
    if request.url.path == "/api/v2/selections:estimate":
        return httpx.Response(
            200,
            json={
                "normalized_selection": body,
                "selection_hash": "hash",
                "execution_tier": "synchronous",
                "work": {"result_cells": 20},
            },
        )
    if request.url.path == "/api/v2/search":
        return httpx.Response(200, json={"results": HITS, "database_id": body["database_id"]})
    return httpx.Response(404, json={"error": {"code": "not_found", "message": request.url.path}})


def _transport(seen, warnings=()):
    return httpx.MockTransport(functools.partial(_handler, seen, list(warnings)))


def _install_client(monkeypatch, warnings=()):
    seen = []
    monkeypatch.setattr(
        cli,
        "Client",
        functools.partial(
            Client,
            api_key="token",
            api_url="https://api.example.test",
            transport=_transport(seen, warnings),
        ),
    )
    return seen


def _sent(seen, path="/api/v2/selections"):
    bodies = [json.loads(r.content) for r in seen if r.url.path == path]
    assert len(bodies) == 1, [r.url.path for r in seen]
    return bodies[0]


def _structure(condition_id, smarts, **count):
    return {
        "type": "structure",
        "id": condition_id,
        "relation": "contains",
        "query": {"format": "smarts", "value": smarts},
        "count": {"min": 1, **count},
    }


def _base(database, *, metric="rdkit.ecfp4_tanimoto", limit=20):
    return {
        "schema_version": "molecule-selection/1",
        "database": {"database_id": database},
        "references": [{"id": "query", "structure": {"format": "smiles", "value": QUERY}}],
        "strategy": {"type": "ranked"},
        "objectives": [
            {
                "type": "similarity",
                "reference_id": "query",
                "metric_id": metric,
                "direction": "maximize",
            }
        ],
        "portfolio": {"limit": limit},
        "execution": {"quality": "balanced"},
        "include": [],
    }


AMINO_ACID_SELECTION = {
    **_base("synple-synple-2025-10"),
    "constraints": {
        "structures": {
            "all": [_structure("amino-acids", ALPHA_AMINO_ACID)],
            "any": {"minimum": 0, "conditions": []},
            "none": [],
        }
    },
}


def test_cli_subset_sends_one_similarity_selection_with_the_structure(monkeypatch, capsys):
    seen = _install_client(monkeypatch)
    assert cli.main(["search", QUERY, "-d", "synple", "--subset", "amino-acids"]) == 0
    assert _sent(seen) == AMINO_ACID_SELECTION
    assert not [r for r in seen if r.url.path == "/api/v2/search"]
    out = capsys.readouterr().out
    assert "CC(=O)N[C@@H](C)C(=O)O" in out
    assert "Filters (checked by the server on every result):" in out
    assert "amino-acids: Contains an alpha-amino-acid residue" in out


def test_database_suffix_is_the_same_subset(monkeypatch):
    seen = _install_client(monkeypatch)
    assert cli.main(["search", QUERY, "-d", "synple:amino-acids"]) == 0
    assert _sent(seen) == AMINO_ACID_SELECTION


def test_cli_combines_preset_properties_and_adhoc_patterns(monkeypatch):
    seen = _install_client(monkeypatch)
    argv = [
        "search",
        QUERY,
        "-d",
        "enamine:ro5",
        "-m",
        "shape",
        "-n",
        "50",
        "--where",
        "mw<=450",
        "--where",
        "tpsa=40..90",
        "--require",
        "c1ccncc1",
        "--require-count",
        "1-2",
        "[NX3;H2]",
        "--require-count",
        "2-",
        "C(=O)N",
        "--exclude",
        "[N+](=O)[O-]",
    ]
    assert cli.main(argv) == 0

    def prop(property_id, units, operator, value):
        return {
            "type": "property",
            "property_id": property_id,
            "operator": operator,
            "value": value,
            "units": units,
            "required_fidelity": "exact_product",
            "missing": "reject",
        }

    assert _sent(seen) == {
        **_base("enamine-real-v5a", metric="cheese.shape", limit=50),
        "constraints": {
            "presets": [{"preset_id": "lipinski-ro5", "version": "1"}],
            "properties": [
                prop("rdkit.mol_wt", "Da", "lte", 450.0),
                prop("rdkit.tpsa", "A^2", "range", {"lower": 40.0, "upper": 90.0}),
            ],
            "structures": {
                "all": [
                    _structure("require-1", "c1ccncc1"),
                    _structure("require-2", "[NX3;H2]", max=2),
                    _structure("require-3", "C(=O)N", min=2),
                ],
                "any": {"minimum": 0, "conditions": []},
                "none": [_structure("exclude-1", "[N+](=O)[O-]")],
            },
        },
        "include": ["properties"],
    }


def test_python_search_with_subset_returns_a_selection_result():
    seen = []
    with Client(
        api_key="token", api_url="https://api.example.test", transport=_transport(seen)
    ) as client:
        result = client.search(QUERY, database="synple", subset="amino-acids")
    assert _sent(seen) == AMINO_ACID_SELECTION
    assert isinstance(result, SelectionResult)
    assert result.method == "morgan"
    assert result.smiles == [hit["smiles"] for hit in HITS]
    assert result.meta.database == "synple-synple-2025-10"


def test_module_search_passes_filters_and_keeps_plain_search_unchanged():
    seen = []
    deepmedchem.search(
        QUERY,
        database="enamine",
        require=["c1ccncc1"],
        exclude="[N+](=O)[O-]",
        where={"hbd": "<=2"},
        api_key="token",
        api_url="https://api.example.test",
        transport=_transport(seen),
    )
    constraints = _sent(seen)["constraints"]
    assert constraints["structures"]["all"] == [_structure("require-1", "c1ccncc1")]
    assert constraints["structures"]["none"] == [_structure("exclude-1", "[N+](=O)[O-]")]
    assert constraints["properties"][0]["property_id"] == "rdkit.hbd"

    seen.clear()
    deepmedchem.search(
        QUERY,
        database="enamine",
        api_key="token",
        api_url="https://api.example.test",
        transport=_transport(seen),
    )
    assert [r.url.path for r in seen] == ["/api/v2/search"]


def test_druglike_subset_spells_out_the_navigator_window():
    selection = Selection.from_database("enamine").subset("druglike").to_dict()
    conditions = {
        (item["property_id"], item["operator"], json.dumps(item["value"], sort_keys=True))
        for item in selection["constraints"]["properties"]
    }
    assert conditions == {
        ("rdkit.mol_wt", "lte", "500"),
        ("rdkit.crippen_logp", "range", '{"lower": -10, "upper": 5}'),
        ("rdkit.hba", "lte", "10"),
        ("rdkit.hbd", "lte", "5"),
        ("rdkit.rotatable_bonds", "lte", "10"),
        ("rdkit.tpsa", "lte", "140"),
        ("rdkit.fraction_csp3", "range", '{"lower": 0.1, "upper": 0.8}'),
        ("rdkit.aromatic_rings", "range", '{"lower": 1, "upper": 5}'),
    }


def test_require_any_subset_uses_minimum_one():
    selection = Selection.from_database("enamine").subset("covalent-warheads").to_dict()
    group = selection["constraints"]["structures"]["any"]
    assert group["minimum"] == 1
    assert [item["id"] for item in group["conditions"]] == [
        "covalent-warheads",
        "covalent-warheads-2",
        "covalent-warheads-3",
        "covalent-warheads-4",
        "covalent-warheads-5",
    ]


def test_sample_with_property_subset_sends_a_sample_selection(monkeypatch):
    seen = _install_client(monkeypatch)
    assert cli.main(["sample", "-d", "enamine", "--subset", "ro5", "-n", "5", "--seed", "7"]) == 0
    assert _sent(seen) == {
        "schema_version": "molecule-selection/1",
        "database": {"database_id": "enamine-real-v5a"},
        "references": [],
        "strategy": {"type": "sample", "distribution": "route_product_tuple"},
        "constraints": {"presets": [{"preset_id": "lipinski-ro5", "version": "1"}]},
        "objectives": [],
        "portfolio": {"limit": 5},
        "execution": {"quality": "balanced", "seed": 7},
        "include": ["properties"],
    }


def test_sample_with_structure_subset_fails_before_any_request(monkeypatch, capsys):
    seen = _install_client(monkeypatch)
    assert cli.main(["sample", "-d", "synple:amino-acids"]) == 1
    assert "sampling with structure filters is not available" in capsys.readouterr().err
    assert seen == []


def test_invalid_smarts_fails_before_any_request(monkeypatch, capsys):
    pytest.importorskip("rdkit")
    seen = _install_client(monkeypatch)
    assert cli.main(["search", QUERY, "-d", "enamine", "--require", "C(=O"]) == 1
    assert "invalid SMARTS pattern" in capsys.readouterr().err
    assert seen == []


def test_filtered_limit_above_the_api_maximum_is_rejected(monkeypatch, capsys):
    seen = _install_client(monkeypatch)
    assert cli.main(["search", QUERY, "-d", "enamine:ro5", "-n", "500"]) == 1
    assert "1 to 200 hits" in capsys.readouterr().err
    assert seen == []


def test_explain_validates_and_estimates_without_running(monkeypatch, capsys):
    seen = _install_client(monkeypatch)
    assert cli.main(["search", QUERY, "-d", "synple", "--subset", "aa", "--explain"]) == 0
    paths = [r.url.path for r in seen]
    assert paths == ["/api/v2/selections:validate", "/api/v2/selections:estimate"]
    assert _sent(seen, "/api/v2/selections:validate") == AMINO_ACID_SELECTION
    out = capsys.readouterr().out
    assert "constraints.structures.all[0]: conservative_approximate_pushdown" in out
    assert "execution: synchronous" in out
    assert "no credit was used" in out


def test_partial_results_are_reported(monkeypatch, capsys):
    _install_client(
        monkeypatch, warnings=[{"code": "partial_results", "message": "fewer than requested"}]
    )
    assert cli.main(["search", QUERY, "-d", "synple:amino-acids"]) == 0
    err = capsys.readouterr().err
    assert "Only 2 of 20 requested molecules satisfy the filters" in err


def test_batch_search_rejects_filters(monkeypatch, tmp_path, capsys):
    seen = _install_client(monkeypatch)
    queries = tmp_path / "q.smi"
    queries.write_text(f"{QUERY}\n")
    assert cli.main(["search", "-i", str(queries), "-d", "synple:amino-acids", "--yes"]) == 1
    assert "batch search (--input) does not take them yet" in capsys.readouterr().err
    assert seen == []


def test_async_search_with_subset():
    seen = []

    async def run():
        async with AsyncClient(
            api_key="token", api_url="https://api.example.test", transport=_transport(seen)
        ) as client:
            return await client.search(QUERY, database="synple:amino-acids")

    result = asyncio.run(run())
    assert _sent(seen) == AMINO_ACID_SELECTION
    assert isinstance(result, SelectionResult)


def test_subsets_command_lists_the_registry(capsys):
    assert cli.main(["subsets"]) == 0
    out = capsys.readouterr().out
    for name in SUBSETS:
        assert name in out
    assert cli.main(["subsets", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["amino-acids"]["definition"] == f"contains {ALPHA_AMINO_ACID}"
    assert payload["ro5"]["kind"] == "property"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mw<=450", PropertyCondition("rdkit.mol_wt", "Da", "lte", 450.0)),
        ("logp < 4", PropertyCondition("rdkit.crippen_logp", "1", "lt", 4.0)),
        ("rdkit.tpsa>=20", PropertyCondition("rdkit.tpsa", "A^2", "gte", 20.0)),
        ("tpsa=40..90", PropertyCondition("rdkit.tpsa", "A^2", "range", (40.0, 90.0))),
        ("1<=hbd<=3", PropertyCondition("rdkit.hbd", "count", "range", (1.0, 3.0))),
        ("rotatable_bonds>2", PropertyCondition("rdkit.rotatable_bonds", "count", "gt", 2.0)),
    ],
)
def test_parse_condition(text, expected):
    assert parse_condition(text) == expected


@pytest.mark.parametrize("text", ["price<10", "mw==3", "tpsa=90..40", "mw"])
def test_parse_condition_rejects_unknown_or_malformed(text):
    with pytest.raises(ValueError):
        parse_condition(text)


def test_subset_names_aliases_and_versions():
    assert get_subset("AA").name == "amino-acids"
    assert get_subset("lipinski/v1").name == "ro5"
    with pytest.raises(ValueError, match="known subsets: .*amino-acids"):
        get_subset("peptides")
    with pytest.raises(ValueError):
        get_subset("ro5/v9")
    assert split_database("synple:amino-acids,ro5") == ("synple", ("amino-acids", "ro5"))
    assert split_database("synple") == ("synple", ())
    assert not Filters.build()
    assert Filters.build(subset="ro5") and not Filters.build(subset="ro5").structural
    assert Filters.build(subset="aa").structural


def test_registry_patterns_parse_and_match_their_intent():
    chem = pytest.importorskip("rdkit.Chem")

    def matches(name, smiles):
        subset = get_subset(name)
        molecule = chem.MolFromSmiles(smiles)
        required = all(
            molecule.HasSubstructMatch(chem.MolFromSmarts(p.smarts)) for p in subset.require
        )
        any_of = not subset.require_any or any(
            molecule.HasSubstructMatch(chem.MolFromSmarts(p.smarts)) for p in subset.require_any
        )
        return required and any_of

    for subset in SUBSETS.values():
        for pattern in (*subset.require, *subset.require_any, *subset.exclude):
            assert chem.MolFromSmarts(pattern.smarts) is not None, pattern
    assert matches("amino-acids", "N[C@@H](C)C(=O)O")  # alanine
    assert matches("amino-acids", "OC(=O)[C@@H]1CCCN1C(=O)OC(C)(C)C")  # Boc-proline
    assert matches("amino-acids", "CC(=O)NCC(=O)OC")  # N-acetyl glycine methyl ester
    assert not matches("amino-acids", "NCCC(=O)O")  # beta-alanine
    assert matches("beta-amino-acids", "NCCC(=O)O")
    assert not matches("amino-acids", "NCC(=O)NC")  # glycinamide: no acid or ester
    fmoc_phe = "O=C(O)[C@H](Cc1ccccc1)NC(=O)OCC1c2ccccc2-c2ccccc21"
    assert matches("amino-acid-building-blocks", fmoc_phe)
    assert not matches("amino-acid-building-blocks", "CC(=O)N[C@@H](C)C(=O)O")  # acylated N
    assert matches("carboxylic-acids", "OC(=O)c1ccccc1")
    assert not matches("carboxylic-acids", "COC(=O)c1ccccc1")
    assert matches("primary-amines", "NCc1ccccc1")
    assert not matches("primary-amines", "NC(=O)c1ccccc1")
    assert matches("acrylamides", "C=CC(=O)Nc1ccccc1")
    assert not matches("acrylamides", "O=C1C=CC(=O)N1")  # maleimide ring
    assert matches("covalent-warheads", "ClCC(=O)Nc1ccccc1")
    assert matches("covalent-warheads", "O=S(=O)(F)c1ccccc1")
