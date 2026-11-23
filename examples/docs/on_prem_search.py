"""Search your own CHEESE installation using the bundled DeepMedChem SDK.

In a configured notebook environment, run this file as-is. From another shell:
    python examples/docs/on_prem_search.py --site https://cheese.your-pharma-company.com
"""

import argparse

from deepmedchem import Client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", help="CHEESE origin (defaults to DEEPMEDCHEM_ON_PREM_URL)")
    parser.add_argument("--database", default="mcule-aa-in-stock", help="Installed library ID")
    args = parser.parse_args()

    with Client(on_prem=True, api_url=args.site) as cheese:
        libraries = [row["database_id"] for row in cheese.catalog()["libraries"]]
        print("Installed libraries:", ", ".join(libraries))
        if args.database not in libraries:
            parser.exit(2, "Choose an installed library with --database.\n")
        result = cheese.search("O=C(O)CNc1ccc(Br)c(Cl)c1C(=O)O", database=args.database, limit=3)
        for hit in result.hits:
            print(hit.catalog_id, hit.score)


if __name__ == "__main__":
    main()
