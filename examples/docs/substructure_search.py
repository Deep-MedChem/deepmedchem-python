from deepmedchem import Client

DATABASE = "enamine-real-v5a"

# Concrete junction-spanning structures should be identified as SMILES.
JUNCTION_SMILES = "CNC(=O)N1CCC1"

# These are genuine query SMARTS: their atom-list, ring-membership, charge,
# and recursive expressions are not ordinary molecular SMILES.
SMARTS_QUERIES = {
    "acyclic hydrazide": "[N;R0][N;R0]C(=O)",
    "cationic carbonyl": "C(=O)C[N+,n+]",
    "amino acid": (
        "[$([NH2]),$([NH][c,CX4]),$(N([c,CX4])[c,CX4]);"
        "!$(NC=O)][CX4]C(=O)[OH]"
    ),
}

with Client(api_key="...") as dmc:
    junction = dmc.search_substructure(
        JUNCTION_SMILES,
        query_format="smiles",
        database=DATABASE,
        limit=3,
    )
    print(f"junction SMILES: {len(junction.results)} hits")

    for name, smarts in SMARTS_QUERIES.items():
        result = dmc.search_substructure(
            smarts,
            query_format="smarts",
            database=DATABASE,
            limit=3,
            timeout_seconds=60,
        )
        print(f"{name}: {len(result.results)} hits")
