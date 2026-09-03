from deepmedchem import Client, Selection

selection = (
    Selection.from_database("freedom-space-5")
    .sample(seed=42)
    .require_preset("lipinski-ro5/v1")
    .where("rdkit.mol_wt", lte=450, units="Da")
    .include("properties")
    .limit(100)
)

with Client() as dmc:
    result = dmc.selections.create(selection)

print(f"Received {len(result)} exact-verified molecules")
