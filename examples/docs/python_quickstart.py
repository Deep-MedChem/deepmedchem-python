from deepmedchem import Client

with Client() as dmc:
    result = dmc.search("CCO", database="enamine-real-v5a", limit=20)
    shape_hits = dmc.search_cheese(
        "CCO", database="enamine-real-v5a", scorer="shape", limit=20
    )
    motif_hits = dmc.search_substructure(
        "C(=O)N1CCC1", query_format="smarts", database="enamine-real-v5a"
    )
    molecules = dmc.sample(database="enamine-real-v5a", count=100, seed=12345)
