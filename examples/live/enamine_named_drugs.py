"""Search Enamine REAL with named-drug queries used in the retrieval experiments."""

from deepmedchem import Client

DATABASE = "enamine-real-v5a"
QUERIES = {
    "Aspirin": "CC(=O)OC1=CC=CC=C1C(=O)O",
    "Caffeine": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",
    "Olanzapine": "CC1=CC2=C(S1)NC3=CC=CC=C3N=C2N4CCN(CC4)C",
}


def main() -> None:
    with Client() as dmc:
        for query_name, query_smiles in QUERIES.items():
            result = dmc.search(query_smiles, database=DATABASE, limit=3)
            print(f"\n{query_name}: {len(result.results)} hits")
            print(f"database={result.database_id} release={result.database_release}")
            for hit in result.results:
                print(
                    f"  {hit.get('rank', '?'):>2}  score={hit.get('score', 0):.4f}  "
                    f"product={hit.get('product_id')}"
                )
                print(f"      {hit.get('smiles')}")


if __name__ == "__main__":
    main()
