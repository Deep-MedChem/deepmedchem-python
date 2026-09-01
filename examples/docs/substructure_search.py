import deepmedchem as dmc

DATABASE = "enamine-real-v5a"

# Concrete junction-spanning structures should be identified as SMILES.
JUNCTION_SMILES = "CNC(=O)N1CCC1"

# These are genuine query SMARTS: their atom-list, ring-membership, charge,
# and recursive expressions are not ordinary molecular SMILES.
SMARTS_QUERIES = {
    "acyclic hydrazide": "[N;R0][N;R0]C(=O)",
    "acyclic urea": "[N;R0]C(=O)[N;R0]",
    "protic amide or acid": "[O,N;H1]C(=O)",
}

junction = dmc.substructure(
    JUNCTION_SMILES,
    format="smiles",
    database=DATABASE,
    limit=3,
)
print(f"junction SMILES: {len(junction)} hits")

for name, smarts in SMARTS_QUERIES.items():
    result = dmc.substructure(
        smarts,
        format="smarts",
        database=DATABASE,
        limit=3,
        timeout_seconds=60,
    )
    print(f"{name}: {len(result)} hits")
