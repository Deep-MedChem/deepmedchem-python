"""Run a resumable QED optimization: python optimization_test.py.

Requires RDKit and DeepMedChem credentials (run ``dmc login`` first).
Re-running resumes the named optimization and exports its results again.
"""

from rdkit import Chem
from rdkit.Chem import QED

import deepmedchem as dmc


def score(smiles):
    """Return one QED value per SMILES, or None for an invalid molecule."""
    return [QED.qed(m) if (m := Chem.MolFromSmiles(s)) else None for s in smiles]


def main():
    result = dmc.optimize(
        score,
        direction="maximize",
        database="enamine",
        budget=500,
        batch_size=50,
        name="qed-demo",
    )
    print(result.top(10))
    result.to_csv("qed_demo.csv")


if __name__ == "__main__":
    main()
