import csv
import json

import pytest

from deepmedchem.export import infer_format, write_result
from deepmedchem.models import SearchResult

RESULT = SearchResult.model_validate(
    {
        "database_id": "db",
        "scorer": "morgan",
        "metric": "ECFP4 Tanimoto",
        "results": [
            {
                "rank": 1,
                "smiles": "CCO",
                "score": 1.0,
                "price": 163,
                "product_id": "p1",
                "properties": {"rdkit.mol_wt": 46.07},
                "synthons": None,
            },
            {"rank": 2, "smiles": "CCN", "score": 0.5, "price": None, "product_id": None},
        ],
    }
)


def test_infer_format_from_suffix() -> None:
    assert infer_format("hits.CSV") == "csv"
    assert infer_format("hits.sdf") == "sdf"
    assert infer_format("hits.smi") == "smi"
    assert infer_format("hits.json") == "json"
    with pytest.raises(ValueError):
        infer_format("hits.parquet")


def test_csv_columns_are_stable_and_nested_values_are_json(tmp_path) -> None:
    target = tmp_path / "hits.csv"
    assert RESULT.to_csv(target) == 2
    with open(target, newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == [
        "rank",
        "smiles",
        "score",
        "price",
        "product_id",
        "database_id",
        "database_release",
        "reaction_id",
        "metric",
        "properties",
    ]
    assert rows[0]["database_id"] == "db"
    assert json.loads(rows[0]["properties"]) == {"rdkit.mol_wt": 46.07}
    assert rows[1]["price"] == ""
    assert rows[1]["properties"] == ""


def test_json_and_smi_writers(tmp_path) -> None:
    assert write_result(RESULT, tmp_path / "hits.json") == 2
    payload = json.loads((tmp_path / "hits.json").read_text())
    assert payload["results"][0]["smiles"] == "CCO"
    assert RESULT.to_file(tmp_path / "hits.smi") == 2
    assert (tmp_path / "hits.smi").read_text() == "CCO p1\nCCN 2\n"


def test_sdf_requires_rdkit_or_carries_tags(tmp_path) -> None:
    rdkit = pytest.importorskip("rdkit")
    from rdkit import Chem

    target = tmp_path / "hits.sdf"
    assert RESULT.to_sdf(target) == 2
    molecules = [mol for mol in Chem.SDMolSupplier(str(target)) if mol is not None]
    assert [mol.GetProp("_Name") for mol in molecules] == ["p1", "CCN"]
    assert molecules[0].GetProp("price") == "163"
    assert molecules[0].GetProp("score") == "1.0"
    assert not molecules[1].HasProp("price")
    assert rdkit.__version__


def test_sdf_without_rdkit_explains_the_install(monkeypatch, tmp_path) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("rdkit"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match="pip install rdkit"):
        RESULT.to_sdf(tmp_path / "hits.sdf")
