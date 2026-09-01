from deepmedchem import Client

dmc = Client(api_key="...")

result = dmc.search(
    "CC(=O)OC1=CC=CC=C1C(=O)O",
    database="enamine-real-v5a",
    limit=3,
)

print(f"database={result.database_id} release={result.database_release}")
for hit in result.results:
    print(f"{hit['rank']}  score={hit['score']:.4f}  {hit['smiles']}")

dmc.close()
