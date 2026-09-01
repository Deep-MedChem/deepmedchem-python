import deepmedchem as dmc

result = dmc.search(
    "CC(=O)OC1=CC=CC=C1C(=O)O",
    database="enamine-real-v5a",
    method="shape",
    limit=3,
)

print(result)
for hit in result.hits:
    print(f"{hit.rank}  score={hit.score:.4f}  {hit.smiles}")
