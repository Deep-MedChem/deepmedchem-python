"""Let the service enforce hard limits, and score the soft preferences yourself.

Two different mechanisms, often confused:

* ``properties=`` are **hard limits**. Navigator only ever proposes molecules inside them, so
  none of your budget is spent on molecules you would have thrown away. They are exact windows
  on RDKit descriptors, evaluated on the assembled product.
* the **score** your function returns is a soft preference. It cannot exclude anything; it ranks
  what is proposed and steers the next round.

A descriptor can be used both ways at once, which is what this example does with logP: the window
refuses anything above ``--logp-max``, and the score prefers the lowest logP inside that window.
``catalog()["optimization"]["properties"]`` lists the descriptors your account may constrain.

Requirements::

    pip install deepmedchem rdkit

Run::

    python 06_hard_limits_soft_score.py --budget 150 --batch-size 50
"""

from __future__ import annotations

import argparse

from rdkit import Chem, RDLogger
from rdkit.Chem import QED, Crippen, Descriptors, rdMolDescriptors

import deepmedchem as dmc

RDLogger.DisableLog("rdApp.*")


def desirability(qed: float, logp: float, logp_max: float) -> float:
    """Prefer a high QED and, inside the allowed window, the lowest logP."""

    headroom = max(0.0, min(1.0, (logp_max - logp) / 4.0))
    return 0.7 * qed + 0.3 * headroom


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--name", default="hard-limits-soft-score", help="Resume key")
    parser.add_argument("--database", default="enamine")
    parser.add_argument("--budget", type=int, default=150)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--mw-max", type=float, default=400.0)
    parser.add_argument("--logp-max", type=float, default=3.0)
    parser.add_argument("--tpsa", type=float, nargs=2, default=(40.0, 100.0))
    parser.add_argument("--output", default="hard_limits_soft_score.csv")
    args = parser.parse_args()

    tpsa_min, tpsa_max = args.tpsa
    # Checked locally as well, so the example proves the service enforced the windows.
    breaches = 0

    def score(smiles: list[str]) -> list[dict | None]:
        nonlocal breaches
        values: list[dict | None] = []
        for text in smiles:
            molecule = Chem.MolFromSmiles(text)
            if molecule is None:
                values.append(None)
                continue
            weight = Descriptors.MolWt(molecule)
            logp = Crippen.MolLogP(molecule)
            tpsa = rdMolDescriptors.CalcTPSA(molecule)
            if weight > args.mw_max + 1e-6 or logp > args.logp_max + 1e-6:
                breaches += 1
            elif not tpsa_min - 1e-6 <= tpsa <= tpsa_max + 1e-6:
                breaches += 1
            qed = QED.qed(molecule)
            values.append(
                {
                    "score": desirability(qed, logp, args.logp_max),
                    "qed": round(qed, 4),
                    "mw": round(weight, 1),
                    "logp": round(logp, 3),
                    "tpsa": round(tpsa, 1),
                }
            )
        return values

    result = dmc.optimize(
        score,
        direction="maximize",
        database=args.database,
        budget=args.budget,
        batch_size=args.batch_size,
        name=args.name,
        filters="druglike",
        properties={
            "MolWt": {"max": args.mw_max},
            "MolLogP": {"max": args.logp_max},
            "TPSA": {"min": tpsa_min, "max": tpsa_max},
        },
        scorer={
            "name": "qed_and_logp_headroom",
            "version": "1",
            "settings": {"mw_max": args.mw_max, "logp_max": args.logp_max},
        },
    )

    print(f"\nWindow breaches among {len(result)} proposals: {breaches}")
    print("Top molecules:")
    for observation in result.top(10):
        metrics = observation.metrics
        print(
            f"{observation.score:.3f}  QED {metrics.get('qed', 0):.2f}  "
            f"MW {metrics.get('mw', 0):6.1f}  logP {metrics.get('logp', 0):5.2f}  "
            f"TPSA {metrics.get('tpsa', 0):5.1f}  {observation.smiles}"
        )
    result.to_csv(args.output)
    print(f"\nWrote {len(result)} rows to {args.output}")


if __name__ == "__main__":
    main()
