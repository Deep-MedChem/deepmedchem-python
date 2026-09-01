from deepmedchem import Client, Selection

aspirin = "CC(=O)Oc1ccccc1C(=O)O"
selection = (
    Selection.from_database("enamine-real-v5a", release="2026-08-29.1")
    .reference("aspirin", smiles=aspirin)
    .ranked()
    .maximize_similarity("rdkit.ecfp4_tanimoto", reference="aspirin")
    .require_different_scaffold("rdkit.bemis_murcko", reference="aspirin")
    .require_pattern("alpha-amino-acid/v1", min_count=1)
    .where("rdkit.mol_wt", gt=250, units="Da", fidelity="exact_product")
    .limit(100)
    .max_per_scaffold(5)
    .include("properties", "constraint_evidence", "objective_components", "execution_plan")
)

selection.to_json()
selection.to_yaml()
replayed = Selection.model_validate(selection.to_dict())

with Client() as dmc:
    validation = dmc.selections.validate(replayed)
    estimate = dmc.selections.estimate(validation.normalized_selection)
