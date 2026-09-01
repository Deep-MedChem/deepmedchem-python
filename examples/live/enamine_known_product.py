"""Search with a frozen Enamine-v5a product and inspect its route provenance."""

from deepmedchem import Client

DATABASE = "enamine-real-v5a"
QUERY_ID = "q01"
QUERY_SMILES = (
    "CCC1(CNC(=O)C(c2n[nH]c(C)c2C)N2CCN(Cc3ccc(F)cc3)C[C@@H]2C)OCCO1"
)


def main() -> None:
    with Client() as dmc:
        result = dmc.search(
            QUERY_SMILES,
            database=DATABASE,
            limit=5,
            include_synthons=True,
        )

    print(f"query={QUERY_ID} database={result.database_id} release={result.database_release}")
    for hit in result.results:
        synthons = hit.get("synthons", [])
        print(
            f"{hit.get('rank', '?'):>2}  score={hit.get('score', 0):.4f}  "
            f"exact_query={hit.get('smiles') == QUERY_SMILES}  "
            f"reaction={hit.get('reaction_id')}  synthons={len(synthons)}"
        )
        print(f"    product={hit.get('product_id')}  {hit.get('smiles')}")


if __name__ == "__main__":
    main()
