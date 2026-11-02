"""Run the executable recipes through argument parsing and request construction."""

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import deepmedchem as dmc
from deepmedchem.optimization import build_specification


@pytest.mark.parametrize(
    "filename,extra,properties,objectives",
    [
        ("01_property_logp_qed.py", [], 1, 1),
        ("06_hard_limits_soft_score.py", ["--mw-max", "350", "--tpsa", "20", "80"], 3, 2),
    ],
)
def test_typed_optimization_recipe(monkeypatch, filename, extra, properties, objectives):
    captured = []
    exported = []

    def optimize(**kwargs):
        captured.append(build_specification(**kwargs))
        return SimpleNamespace(top=lambda count: [], to_csv=exported.append)

    script = Path(__file__).resolve().parents[1] / "examples" / "optimization" / filename
    monkeypatch.setattr(dmc, "optimize", optimize)
    monkeypatch.setattr(sys, "argv", [str(script), "--output", "result.csv", *extra])
    runpy.run_path(str(script), run_name="__main__")
    assert exported == ["result.csv"]
    assert len(captured) == 1
    spec = captured[0]
    assert len(spec["constraints"]["properties"]) == properties
    assert len(spec["objectives"]) == objectives
    assert spec["objective"]["direction"] == "maximize"
    if extra:
        assert spec["constraints"]["properties"][0]["value"] == 350
        assert spec["constraints"]["properties"][2]["value"] == {"lower": 20, "upper": 80}
