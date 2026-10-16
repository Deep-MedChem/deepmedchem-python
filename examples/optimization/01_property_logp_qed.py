"""Maximize internal QED while guiding generation into a declared logP window.

Requires only deepmedchem. No RDKit installation or scoring callback is needed.
"""

from __future__ import annotations

import argparse
import deepmedchem as dmc


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--name", default="internal-qed-logp")
    parser.add_argument("--database", default="enamine")
    parser.add_argument("--budget", type=int, default=300)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--output", default="property_qed_logp.csv")
    args = parser.parse_args()
    result = dmc.optimize(
        database=args.database,
        budget=args.budget,
        batch_size=args.batch_size,
        name=args.name,
        filters="druglike",
        properties={"MolLogP": (0, 3)},
        objectives=[{"type": "property", "property_id": "rdkit.qed", "lower": 0, "upper": 1}],
    )
    for row in result.top(10):
        print(row.score, row.metrics.get("objective_0"), row.smiles)
    result.to_csv(args.output)


if __name__ == "__main__":
    main()
