"""Optimize the prediction of your own machine-learning model.

Any object with a scikit-learn style ``predict(X)`` works: a random forest, gradient boosting, a
neural network wrapped in a small class, or a model loaded from disk with joblib. Molecules are
featurized with Morgan fingerprints; change ``featurize`` to match how your model was trained.

The prediction is the optimized score. Everything else you compute (here the spread across the
forest's trees and the similarity to the nearest training molecule, a simple applicability-domain
check) goes into metrics: stored with the results, never optimized.

Requirements::

    pip install deepmedchem rdkit scikit-learn numpy   # joblib comes with scikit-learn

Run with the tiny placeholder model trained below (for a demonstration only)::

    python 02_your_ml_model.py --budget 200 --batch-size 50

Or with your own model::

    python 02_your_ml_model.py --model my_model.joblib --model-version 2026-10-01
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
from rdkit import Chem, DataStructs, RDLogger
from rdkit.Chem import rdFingerprintGenerator

import deepmedchem as dmc

RDLogger.DisableLog("rdApp.*")

RADIUS, BITS = 2, 2048
_GENERATOR = rdFingerprintGenerator.GetMorganGenerator(radius=RADIUS, fpSize=BITS)

# --- PLACEHOLDER DATA -----------------------------------------------------------------------
# These labels are MADE UP so the example runs end to end. They are not measurements and the
# resulting model predicts nothing real. Replace them with your own assay data, or pass --model.
PLACEHOLDER_TRAINING_SET = [
    ("CC(=O)Nc1ccc(O)cc1", 5.1),
    ("CC(C)Cc1ccc(C(C)C(=O)O)cc1", 5.6),
    ("COc1ccc2[nH]cc(CCN)c2c1", 6.2),
    ("O=C(O)c1ccccc1O", 4.3),
    ("CN1CCC[C@H]1c1cccnc1", 5.8),
    ("Cc1ccc(S(=O)(=O)N)cc1", 4.9),
    ("O=C(Nc1ccccc1)c1ccncc1", 6.0),
    ("c1ccc2c(c1)[nH]c1ccccc12", 6.4),
    ("CC(=O)Oc1ccccc1C(=O)O", 4.6),
    ("Clc1ccc(cc1)C(=O)N1CCCC1", 5.9),
    ("NC(=O)c1cccnc1", 4.4),
    ("COc1ccc(cc1)C(=O)Nc1ccc(F)cc1", 6.7),
]
# ---------------------------------------------------------------------------------------------


def fingerprint(smiles: str):
    molecule = Chem.MolFromSmiles(smiles)
    return None if molecule is None else _GENERATOR.GetFingerprint(molecule)


def featurize(fingerprints) -> np.ndarray:
    matrix = np.zeros((len(fingerprints), BITS), dtype=np.uint8)
    for row, bits in enumerate(fingerprints):
        DataStructs.ConvertToNumpyArray(bits, matrix[row])
    return matrix


def train_placeholder_model():
    from sklearn.ensemble import RandomForestRegressor

    fingerprints = [fingerprint(smiles) for smiles, _ in PLACEHOLDER_TRAINING_SET]
    labels = [label for _, label in PLACEHOLDER_TRAINING_SET]
    model = RandomForestRegressor(n_estimators=200, random_state=0)
    model.fit(featurize(fingerprints), labels)
    return model, fingerprints


def make_scorer(model, training_fingerprints):
    def score(smiles: list[str]) -> list[dict | None]:
        fingerprints = [fingerprint(text) for text in smiles]
        usable = [index for index, bits in enumerate(fingerprints) if bits is not None]
        values: list[dict | None] = [None] * len(smiles)  # None = failed (unparseable SMILES)
        if not usable:
            return values
        features = featurize([fingerprints[index] for index in usable])
        predictions = model.predict(features)
        # Optional extras for the metrics side channel.
        spread = None
        if hasattr(model, "estimators_"):
            per_tree = np.stack([tree.predict(features) for tree in model.estimators_])
            spread = per_tree.std(axis=0)
        for position, index in enumerate(usable):
            nearest = (
                max(DataStructs.BulkTanimotoSimilarity(fingerprints[index], training_fingerprints))
                if training_fingerprints
                else None
            )
            values[index] = {
                "score": predictions[position],  # NumPy scalars are fine
                "prediction_std": None if spread is None else spread[position],
                "nearest_training_similarity": nearest,
            }
        return values

    return score


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", type=Path, help="A joblib-saved model with .predict(X)")
    parser.add_argument("--model-version", help="Recorded in the scorer identity")
    parser.add_argument("--training-smiles", type=Path, help="One SMILES per line (for metrics)")
    parser.add_argument("--name", default="ml-model-demo", help="Optimization name (resume key)")
    parser.add_argument("--database", default="enamine")
    parser.add_argument("--budget", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--output", default="ml_model_results.csv")
    args = parser.parse_args()

    if args.model:
        import joblib

        model = joblib.load(args.model)
        version = args.model_version or hashlib.sha256(args.model.read_bytes()).hexdigest()[:16]
        training = []
        if args.training_smiles:
            lines = args.training_smiles.read_text().split()
            training = [bits for bits in map(fingerprint, lines) if bits is not None]
    else:
        print("Using the PLACEHOLDER model trained on made-up labels; pass --model for real use.")
        model, training = train_placeholder_model()
        version = "placeholder-1"

    result = dmc.optimize(
        make_scorer(model, training),
        direction="maximize",  # e.g. a predicted pIC50: higher is better
        database=args.database,
        budget=args.budget,
        batch_size=args.batch_size,
        name=args.name,
        filters="druglike",
        scorer={
            "name": type(model).__name__,
            "version": version,
            "settings": {"features": f"morgan r={RADIUS} {BITS} bits"},
        },
    )

    print(f"\nTop predictions of {len(result)} scored:")
    for observation in result.top(10):
        similarity = observation.metrics.get("nearest_training_similarity")
        similarity_text = "-" if similarity is None else f"{similarity:.2f}"
        print(f"{observation.score:.2f}  nearest-train-sim {similarity_text}  {observation.smiles}")
    result.to_csv(args.output)
    print(f"\nWrote {len(result)} rows to {args.output}")


if __name__ == "__main__":
    main()
