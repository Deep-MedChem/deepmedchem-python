"""Work from a required substructure: search for the motif, then rank the hits locally.

**Read this before reaching for an optimization run.** A hosted optimization cannot be
restricted to a substructure: ``properties=`` constrains RDKit descriptors, ``filters="druglike"``
applies the druglike gate, and that is all the service enforces. Rewarding the motif in your
score does not work either — in a space this large, essentially none of the proposals contain a
specific motif, so the surrogate never sees a positive example and has nothing to learn from.
Measured on a 150-molecule run with a dominant acrylamide reward: 0 matches.

So when a motif is a *requirement*, start from substructure search, which does take SMARTS, and
rank its hits with the same scoring function you would have optimized. That is exhaustive within
the limit you ask for rather than adaptive, which for a narrowly defined motif is usually what
you wanted anyway.

What would need to change for a genuine motif-constrained optimization: SMARTS constraints in the
optimization gate, or a decomposition of a reference molecule into synthons so it can seed analog
search. Substructure hits cannot stand in for the latter today: their ``product_id`` belongs to
the platform search, not to Navigator's synthon space, and seeding rejects it as malformed.

Requirements::

    pip install deepmedchem rdkit

Run::

    python 07_motif_focused_screening.py 'C=CC(=O)N' --format smiles --limit 200
    python 07_motif_focused_screening.py '[CX3]=[CX3]-[CX3](=O)-[NX3]' --limit 200
"""

from __future__ import annotations

import argparse
import csv

from rdkit import Chem, RDLogger
from rdkit.Chem import QED, Crippen, Descriptors

import deepmedchem as dmc

RDLogger.DisableLog("rdApp.*")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("query", help="Substructure query, SMARTS by default")
    parser.add_argument("--format", default="smarts", choices=("smarts", "smiles"))
    parser.add_argument("--database", default="enamine")
    parser.add_argument("--limit", type=int, default=200, help="Hits to retrieve")
    parser.add_argument("--top", type=int, default=10, help="Rows to print")
    parser.add_argument("--max-logp", type=float, default=None, help="Drop hits above this logP")
    parser.add_argument("--output", default="motif_hits.csv")
    args = parser.parse_args()

    found = dmc.substructure(
        args.query, format=args.format, database=args.database, limit=args.limit
    )
    print(f"{len(found.results)} hits in {found.database_id}@{found.database_release}")

    rows = []
    for hit in found.hits:
        molecule = Chem.MolFromSmiles(hit.smiles)
        if molecule is None:
            continue
        logp = Crippen.MolLogP(molecule)
        if args.max_logp is not None and logp > args.max_logp:
            continue
        rows.append(
            {
                "product_id": hit.product_id,
                "smiles": hit.smiles,
                # The same quantity an optimization would have minimized.
                "logp": round(logp, 3),
                "qed": round(QED.qed(molecule), 4),
                "mw": round(Descriptors.MolWt(molecule), 1),
                "price": getattr(hit, "price", None),
            }
        )
    rows.sort(key=lambda row: row["logp"])

    print(f"{len(rows)} kept after local scoring; lowest logP first:")
    for row in rows[: args.top]:
        print(
            f"logP {row['logp']:6.2f}  QED {row['qed']:.2f}  MW {row['mw']:6.1f}  {row['smiles']}"
        )
    if rows:
        with open(args.output, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nWrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
