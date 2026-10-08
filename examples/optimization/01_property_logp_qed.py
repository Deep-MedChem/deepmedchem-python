"""Your first optimization in about a minute: maximize a desirability score built from QED and logP.

Navigator proposes molecules from a synthon space; this script scores them locally with RDKit and
sends the scores back, round after round, until the budget is spent.

Requirements::

    pip install deepmedchem rdkit
    dmc login                      # or export DEEPMEDCHEM_API_KEY=...

Run::

    python 01_property_logp_qed.py --budget 300 --batch-size 50

Re-running the same command resumes the same optimization (it is identified by ``--name``).
"""

from __future__ import annotations

import argparse
import math

from rdkit import Chem, RDLogger
from rdkit.Chem import QED, Crippen

import deepmedchem as dmc

RDLogger.DisableLog("rdApp.*")

# The logP window we like. Inside it the logP term is 1; outside it falls off smoothly.
LOGP_LOW, LOGP_HIGH, LOGP_WIDTH = 1.0, 3.0, 1.0


def logp_desirability(logp: float) -> float:
    if LOGP_LOW <= logp <= LOGP_HIGH:
        return 1.0
    distance = LOGP_LOW - logp if logp < LOGP_LOW else logp - LOGP_HIGH
    return math.exp(-((distance / LOGP_WIDTH) ** 2))


def score(smiles: list[str]) -> list[dict | None]:
    """One value per SMILES, in order: the optimized score plus metrics for later analysis."""

    values: list[dict | None] = []
    for text in smiles:
        molecule = Chem.MolFromSmiles(text)
        if molecule is None:
            values.append(None)  # reported as failed, never dropped
            continue
        qed = QED.qed(molecule)
        logp = Crippen.MolLogP(molecule)
        values.append(
            {
                "score": qed * logp_desirability(logp),  # what Navigator optimizes
                "qed": round(qed, 4),  # metrics are stored, not optimized
                "logp": round(logp, 3),
            }
        )
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--name", default="property-qed-logp", help="Optimization name (resume key)"
    )
    parser.add_argument("--database", default="enamine", help="enamine, freedom, cheminfinita, ...")
    parser.add_argument("--budget", type=int, default=300, help="Molecules to score in total")
    parser.add_argument("--batch-size", type=int, default=50, help="Molecules per round")
    parser.add_argument("--output", default="property_qed_logp.csv", help="CSV of every result")
    args = parser.parse_args()

    result = dmc.optimize(
        score,
        direction="maximize",
        database=args.database,
        budget=args.budget,
        batch_size=args.batch_size,
        name=args.name,
        filters="druglike",
        # Declaring what produced the scores pins it: a later submission with a different
        # scorer identity is refused instead of silently mixing two different measurements.
        scorer={
            "name": "qed_x_logp_desirability",
            "version": "1",
            "settings": {"logp_window": [LOGP_LOW, LOGP_HIGH], "width": LOGP_WIDTH},
        },
    )

    print(f"\nTop molecules of {len(result)} scored:")
    for observation in result.top(10):
        metrics = observation.metrics
        print(
            f"{observation.score:.3f}  QED {metrics.get('qed', float('nan')):.2f}  "
            f"logP {metrics.get('logp', float('nan')):5.2f}  round {observation.round}  "
            f"{observation.smiles}"
        )
    result.to_csv(args.output)
    print(f"\nWrote {len(result)} rows to {args.output}")


if __name__ == "__main__":
    main()
