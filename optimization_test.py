"""Run a resumable QED optimization: python optimization_test.py.

Requires RDKit and python-dotenv. Put DEEPMEDCHEM_API_KEY in the .env file
beside this script, or authenticate with ``dmc login --profile dev``.

Re-running resumes the named optimization and exports its results again.
This demo defaults to the development API; use --profile to select another profile.
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv
from rdkit import Chem
from rdkit.Chem import QED

import deepmedchem as dmc


def score(smiles):
    """Return one QED value per SMILES, or None for an invalid molecule."""
    return [QED.qed(m) if (m := Chem.MolFromSmiles(s)) else None for s in smiles]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", default="dev", help="DeepMedChem profile (default: dev)"
    )
    args = parser.parse_args()
    load_dotenv(Path(__file__).with_name(".env"), override=False)
    try:
        with dmc.Client(profile=args.profile) as client:
            result = dmc.optimize(
                score,
                direction="maximize",
                database="enamine",
                budget=500,
                batch_size=50,
                name="qed-demo",
                client=client,
            )
    except dmc.DeepMedChemError as error:
        if error.status_code == 404:
            parser.exit(
                1,
                "Optimization request returned HTTP 404. The selected API may not have "
                "optimization support deployed. Production lacked this endpoint on 2026-10-09; "
                "development exposed it. To use development, run `dmc login --profile dev`, "
                "then `python optimization_test.py --profile dev`.\n",
            )
        parser.exit(1, f"Optimization failed: {error}\n")
    print(result.top(10))
    result.to_csv("qed_demo.csv")


if __name__ == "__main__":
    main()
