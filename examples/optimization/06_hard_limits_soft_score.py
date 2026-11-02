"""Declare hard descriptor limits and soft built-in preferences separately.

The logP constraint bounds eligibility; the objective prefers lower logP within
that domain. QED and logP are computed internally, with explicit normalization.
Requires only deepmedchem.
"""

from __future__ import annotations

import argparse

import deepmedchem as dmc


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--name", default="internal-hard-limits-soft-score")
    parser.add_argument("--database", default="enamine")
    parser.add_argument("--budget", type=int, default=150)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--mw-max", type=float, default=400)
    parser.add_argument("--logp-max", type=float, default=3)
    parser.add_argument("--tpsa", type=float, nargs=2, default=(40, 100))
    parser.add_argument("--output", default="hard_limits_soft_score.csv")
    args = parser.parse_args()
    result = dmc.optimize(
        database=args.database,
        budget=args.budget,
        batch_size=args.batch_size,
        name=args.name,
        constraints=[
            dmc.PropertyRange("MolWt", max=args.mw_max),
            dmc.PropertyRange("MolLogP", max=args.logp_max),
            dmc.PropertyRange("TPSA", min=args.tpsa[0], max=args.tpsa[1]),
        ],
        objectives=[
            dmc.Maximize("rdkit.qed", scale=dmc.Scale(lower=0, upper=1), weight=0.7),
            dmc.Minimize(
                "MolLogP",
                scale=dmc.Scale(lower=args.logp_max - 4, upper=args.logp_max),
                weight=0.3,
            ),
        ],
    )
    for row in result.top(10):
        print(row.score, row.metrics, row.smiles)
    result.to_csv(args.output)


if __name__ == "__main__":
    main()
