"""Public value objects: input validation, immutability and the service contract."""

import json

import pytest
from pydantic import ValidationError

import deepmedchem as dmc
from deepmedchem.optimization import build_specification


def specification(**kwargs):
    return build_specification(database="enamine", budget=500, batch_size=50, **kwargs)


def test_readme_example_serializes_to_supported_service_contract():
    spec = specification(
        name="qed-amino-acids",
        constraints=[
            dmc.PropertyRange("MolWt", max=400),
            dmc.PropertyRange("MolLogP", min=0, max=3),
            dmc.RequireStructure("alpha-amino-acid/v1"),
        ],
        objectives=[dmc.Maximize("rdkit.qed", scale=dmc.Scale(lower=0, upper=1))],
    )
    assert spec == {
        "database": "enamine-real-v5a",
        "name": "qed-amino-acids",
        "budget": 500,
        "batch_size": 50,
        "objective": {"direction": "maximize"},
        "constraints": {
            "properties": [
                {"property_id": "rdkit.mol_wt", "units": "Da", "operator": "lte", "value": 400},
                {
                    "property_id": "rdkit.crippen_logp",
                    "units": "1",
                    "operator": "range",
                    "value": {"lower": 0, "upper": 3},
                },
            ],
            "structures": {"all": [{"pattern_id": "alpha-amino-acid/v1"}]},
        },
        "objectives": [
            {
                "type": "property",
                "property_id": "rdkit.qed",
                "direction": "maximize",
                "lower": 0,
                "upper": 1,
                "weight": 1,
            },
        ],
    }
    assert json.loads(json.dumps(spec)) == spec


@pytest.mark.parametrize(
    ("name", "canonical", "units"),
    [
        ("MolWt", "rdkit.mol_wt", "Da"),
        ("mw", "rdkit.mol_wt", "Da"),
        ("rdkit.mol_wt", "rdkit.mol_wt", "Da"),
        ("MolLogP", "rdkit.crippen_logp", "1"),
        ("NumHDonors", "rdkit.hbd", "count"),
        ("NumHAcceptors", "rdkit.hba", "count"),
        ("NumRotatableBonds", "rdkit.rotatable_bonds", "count"),
        ("TPSA", "rdkit.tpsa", "A^2"),
        ("FractionCSP3", "rdkit.fraction_csp3", "1"),
        ("NumAromaticRings", "rdkit.aromatic_rings", "count"),
        ("custom.activity", "custom.activity", "nM"),
    ],
)
def test_aliases_and_custom_property_ids(name, canonical, units):
    constraint = dmc.PropertyRange(name, min=1, units=units)
    spec = specification(direction="minimize", constraints=[constraint])
    assert spec["constraints"]["properties"] == [
        {"property_id": canonical, "units": units, "operator": "gte", "value": 1},
    ]
    objective = dmc.Minimize(name, scale=dmc.Scale(lower=0, upper=10))
    assert specification(objectives=[objective])["objectives"][0]["property_id"] == canonical


def test_all_constraint_kinds_are_conjoined_without_dropping_repeated_properties():
    spec = specification(
        direction="minimize",
        properties={"MolWt": (None, 500)},
        constraints=(
            dmc.PropertyRange("MolWt", min=100),
            dmc.PropertyRange("MolWt", max=400),
            dmc.PredictedPropertyRange("herg", units="pChEMBL", max=6),
            dmc.RequireStructure(smarts="C(=O)N", min_count=2, max_count=3),
            dmc.RequireStructure(smiles="CCO"),
            dmc.ExcludeStructure(smarts="[N+](=O)[O-]"),
            dmc.RequireAnyStructure(
                [
                    dmc.RequireStructure("alpha-amino-acid/v1"),
                    dmc.RequireStructure(smarts="c1ccccc1"),
                ]
            ),
        ),
    )
    assert spec["properties"] == {"MolWt": {"max": 500}}
    assert spec["constraints"] == {
        "properties": [
            {"property_id": "rdkit.mol_wt", "units": "Da", "operator": "gte", "value": 100},
            {"property_id": "rdkit.mol_wt", "units": "Da", "operator": "lte", "value": 400},
        ],
        "predicted_properties": [
            {"endpoint_id": "herg", "units": "pChEMBL", "operator": "lte", "value": 6},
        ],
        "structures": {
            "all": [
                {"query": {"format": "smarts", "value": "C(=O)N"}, "count": {"min": 2, "max": 3}},
                {"query": {"format": "smiles", "value": "CCO"}},
            ],
            "none": [{"query": {"format": "smarts", "value": "[N+](=O)[O-]"}}],
            "any": {
                "minimum": 1,
                "conditions": [
                    {"pattern_id": "alpha-amino-acid/v1"},
                    {"query": {"format": "smarts", "value": "c1ccccc1"}},
                ],
            },
        },
    }


def test_objective_sources_directions_weights_and_legacy_terms():
    raw = {"type": "property", "property_id": "rdkit.qed", "lower": 0, "upper": 1}
    spec = specification(
        objectives=(
            dmc.Minimize(
                dmc.ExternalScore(units="kcal/mol"), scale=dmc.Scale(lower=-12, upper=-4), weight=2
            ),
            dmc.Minimize(
                dmc.PredictedProperty("herg", units="pChEMBL"), scale=dmc.Scale(lower=3, upper=8)
            ),
            dmc.Maximize(dmc.Similarity("CCO"), scale=dmc.Scale(lower=0, upper=1)),
            raw,
        )
    )
    assert spec["objective"] == {"direction": "maximize"}
    assert spec["objectives"] == [
        {
            "type": "external",
            "units": "kcal/mol",
            "direction": "minimize",
            "lower": -12,
            "upper": -4,
            "weight": 2,
        },
        {
            "type": "predicted_property",
            "endpoint_id": "herg",
            "units": "pChEMBL",
            "direction": "minimize",
            "lower": 3,
            "upper": 8,
            "weight": 1,
        },
        {
            "type": "similarity",
            "reference_smiles": "CCO",
            "metric_id": "rdkit.ecfp4_tanimoto",
            "direction": "maximize",
            "lower": 0,
            "upper": 1,
            "weight": 1,
        },
        raw,
    ]
    raw["lower"] = -1
    assert spec["objectives"][-1]["lower"] == 0


@pytest.mark.parametrize("bounds", [{"min": 2, "max": 2}, {"min": -2}, {"max": 0}])
def test_inclusive_ranges_allow_equal_and_one_sided_bounds(bounds):
    assert dmc.PropertyRange("MolLogP", **bounds)
    assert dmc.PredictedPropertyRange("custom", units="1", **bounds)


@pytest.mark.parametrize("bad", [True, False, "1", float("nan"), float("inf"), -float("inf")])
def test_numeric_fields_reject_coercion_and_nonfinite_values(bad):
    for factory in (
        lambda: dmc.PropertyRange("MolWt", max=bad),
        lambda: dmc.PredictedPropertyRange("herg", units="pChEMBL", min=bad),
        lambda: dmc.Scale(lower=bad, upper=2),
        lambda: dmc.Scale(lower=0, upper=bad),
        lambda: dmc.Maximize("rdkit.qed", scale=dmc.Scale(lower=0, upper=1), weight=bad),
    ):
        with pytest.raises(ValueError):
            factory()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: dmc.PropertyRange("MolWt"),
        lambda: dmc.PropertyRange("MolWt", min=500, max=400),
        lambda: dmc.PropertyRange("MolWt", max=400, units="kg"),
        lambda: dmc.PropertyRange("custom", max=400),
        lambda: dmc.PropertyRange(" ", max=400, units="1"),
        lambda: dmc.PropertyRange("custom", max=400, units=""),
        lambda: dmc.PredictedPropertyRange("herg", units="pChEMBL"),
        lambda: dmc.PredictedPropertyRange("herg", units="pChEMBL", min=7, max=6),
        lambda: dmc.Scale(lower=1, upper=1),
        lambda: dmc.Scale(lower=2, upper=1),
        lambda: dmc.RequireStructure(),
        lambda: dmc.RequireStructure("pattern", smarts="C"),
        lambda: dmc.RequireStructure(smiles="C", smarts="C"),
        lambda: dmc.RequireStructure(""),
        lambda: dmc.RequireStructure(smarts=" "),
        lambda: dmc.RequireStructure("pattern", min_count=True),
        lambda: dmc.RequireStructure("pattern", min_count=1.5),
        lambda: dmc.RequireStructure("pattern", min_count=-1),
        lambda: dmc.RequireStructure("pattern", min_count=2, max_count=1),
        lambda: dmc.RequireStructure("pattern", max_count=1001),
        lambda: dmc.RequireAnyStructure([]),
        lambda: dmc.RequireAnyStructure([dmc.RequireStructure("pattern")], minimum=2),
        lambda: dmc.RequireAnyStructure([dmc.ExcludeStructure("pattern")]),
        lambda: dmc.RequireAnyStructure([dmc.RequireStructure("pattern")], minimum=True),
        lambda: dmc.PredictedProperty("", units="1"),
        lambda: dmc.Similarity(""),
        lambda: dmc.Similarity("C", metric_id="unsupported"),
        lambda: dmc.ExternalScore(units=""),
        lambda: dmc.Maximize(" ", scale=dmc.Scale(lower=0, upper=1)),
        lambda: dmc.Minimize("rdkit.qed", scale=dmc.Scale(lower=0, upper=1), weight=0),
        lambda: dmc.Maximize("rdkit.qed", scale=dmc.Scale(lower=0, upper=1), weight=-1),
    ],
)
def test_invalid_value_objects_fail_at_construction(factory):
    with pytest.raises(ValueError):
        factory()


def test_objects_are_immutable_and_do_not_retain_mutable_collections():
    pattern = dmc.RequireStructure("alpha-amino-acid/v1")
    items = [pattern]
    group = dmc.RequireAnyStructure(items)
    items.clear()
    assert group.structures == (pattern,)
    scale = dmc.Scale(lower=0, upper=1)
    objective = dmc.Maximize("rdkit.qed", scale=scale)
    for model, name, value in (
        (pattern, "min_count", 2),
        (scale, "lower", -1),
        (objective, "weight", 2),
        (group, "minimum", 2),
    ):
        with pytest.raises(ValidationError, match="frozen"):
            setattr(model, name, value)


def test_misspelled_model_fields_are_rejected():
    with pytest.raises((TypeError, ValidationError)):
        dmc.PropertyRange("MolWt", maximum=400)
    with pytest.raises(ValidationError):
        dmc.Scale(lower=0, upper=1, uppper=2)


def test_multiple_any_groups_fail_instead_of_changing_boolean_meaning():
    group = dmc.RequireAnyStructure([dmc.RequireStructure("pattern")])
    with pytest.raises(ValueError, match="only one"):
        specification(direction="maximize", constraints=[group, group])


def test_legacy_json_remains_unchanged_and_detached():
    constraints = {"structures": {"all": [{"pattern_id": "pattern"}]}}
    objectives = [{"type": "property", "property_id": "rdkit.qed", "lower": 0, "upper": 1}]
    spec = specification(
        properties={"MolWt": (None, 400)}, constraints=constraints, objectives=objectives
    )
    assert spec["properties"] == {"MolWt": {"max": 400}}
    assert spec["constraints"] == constraints
    assert spec["objectives"] == objectives
    constraints["structures"]["all"][0]["pattern_id"] = "changed"
    assert spec["constraints"]["structures"]["all"][0]["pattern_id"] == "pattern"


@pytest.mark.parametrize("constraints", [[], ()])
def test_empty_constraints_are_equivalent_to_omitting_them(constraints):
    assert "constraints" not in specification(direction="minimize", constraints=constraints)


@pytest.mark.parametrize("kwargs", [{"min_count": 0}, {"max_count": 3}])
def test_exclusions_reject_count_traps(kwargs):
    with pytest.raises(ValueError):
        dmc.ExcludeStructure(smarts="C", **kwargs)


def test_exclusions_never_admit_more_frequent_matches():
    spec = specification(direction="minimize", constraints=[
        dmc.ExcludeStructure(smarts="C", min_count=2),
    ])
    assert spec["constraints"]["structures"]["none"] == [
        {"query": {"format": "smarts", "value": "C"}, "count": {"min": 2}},
    ]
    assert dmc.RequireStructure("x") != dmc.ExcludeStructure("x")


def test_tuple_scale_is_validated_and_normalized_to_immutable_scale():
    assert dmc.Minimize("MolWt", scale=(100, 500)) == dmc.Minimize(
        "MolWt", scale=dmc.Scale(lower=100, upper=500)
    )
    assert isinstance(dmc.Maximize("rdkit.qed", scale=(0, 1)).scale, dmc.Scale)


@pytest.mark.parametrize("scale", [(), (0,), (0, 1, 2), (1, 1), (2, 1),
                                  (True, 1), (0, float("inf")), (float("nan"), 1), ("0", 1)])
def test_invalid_tuple_scales_fail_locally(scale):
    with pytest.raises(ValueError):
        dmc.Maximize("rdkit.qed", scale=scale)


@pytest.mark.parametrize("objectives", [
    [dmc.Maximize("MolWt", scale=(0, 500)), dmc.Minimize("mw", scale=(0, 500))],
    [dmc.Maximize("rdkit.qed", scale=(0, 1)),
     dmc.Minimize(dmc.ExternalScore(), scale=(-12, -4)),
     dmc.Maximize(dmc.ExternalScore(), scale=(-12, -4))],
    [dmc.Maximize(dmc.Similarity("CCO"), scale=(0, 1)),
     dmc.Maximize(dmc.Similarity("CCC"), scale=(0, 1))],
    [dmc.Maximize(dmc.PredictedProperty("herg", units="pChEMBL"), scale=(3, 8)),
     dmc.Minimize(dmc.PredictedProperty("herg", units="pChEMBL"), scale=(3, 8))],
    [dmc.Maximize("MolWt", scale=(0, 500)),
     {"type": "property", "property_id": "rdkit.mol_wt", "lower": 0, "upper": 500}],
])
def test_duplicate_sources_rejected_after_canonicalization(objectives):
    with pytest.raises(ValueError, match="unique"):
        specification(objectives=objectives)


@pytest.mark.parametrize("objective", [
    dmc.Minimize(dmc.ExternalScore(), scale=(-12, -4)),
    {"type": "external", "lower": -12, "upper": -4},
])
def test_external_only_objectives_explain_callback_api(objective):
    with pytest.raises(ValueError, match="external-only.*direction="):
        specification(objectives=[objective])


@pytest.mark.parametrize("factory,limit,path", [
    (lambda: dmc.PropertyRange("MolWt", max=500), 32, "properties"),
    (lambda: dmc.PredictedPropertyRange("herg", units="pChEMBL", max=6),
     32, "predicted_properties"),
    (lambda: dmc.RequireStructure(smarts="C"), 16, "structures.all"),
    (lambda: dmc.ExcludeStructure(smarts="N"), 16, "structures.none"),
])
def test_constraint_caps_apply_to_typed_and_legacy_payloads(factory, limit, path):
    constraints = [factory() for _ in range(limit)]
    raw = specification(direction="minimize", constraints=constraints)["constraints"]
    assert specification(direction="minimize", constraints=raw)["constraints"] == raw
    with pytest.raises(ValueError, match=f"at most {limit}"):
        specification(direction="minimize", constraints=[*constraints, factory()])
    entries = raw
    for component in path.split("."):
        entries = entries[component]
    entries.append(entries[0])
    with pytest.raises(ValueError, match=f"at most {limit}"):
        specification(direction="minimize", constraints=raw)


def test_legacy_require_any_obeys_structure_cap():
    raw = {"structures": {"any": {"minimum": 1, "conditions": [{"pattern_id": "x"}] * 17}}}
    with pytest.raises(ValueError, match="at most 16"):
        specification(direction="minimize", constraints=raw)
