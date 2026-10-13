"""Dock every proposed molecule with GNINA and let Navigator minimize the docking score.

Navigator proposes molecules on DeepMedChem's servers; GNINA docks them on your machine; the
affinities go back and steer the next proposals. Only SMILES and scores cross the network: your
receptor never leaves this machine.

Requirements::

    pip install deepmedchem rdkit
    # the gnina binary: https://github.com/gnina/gnina/releases (or set GNINA_PATH)
    # optional, for pH-dependent protonation: Open Babel (`obabel` on PATH)

Run::

    python 03_gnina_docking.py --receptor receptor.pdb --autobox-ligand reference_ligand.sdf \\
        --name kif11-gnina --budget 1000 --batch-size 100 --workers 4 --cpu 4

or, with an explicit box instead of a reference ligand::

    python 03_gnina_docking.py --receptor receptor.pdb --center 10.2 4.1 -3.0 --size 22 22 22 ...

Resuming: the optimization is identified by ``--name``. Re-running the same command after Ctrl-C,
a crash or a reboot continues where it stopped. Docking results that were computed but not yet
uploaded are re-sent from a local journal instead of being docked again.

Pose selection (read this before comparing numbers): with CNN scoring on (``--cnn-scoring
rescore``, GNINA's default) GNINA writes its poses ordered by CNNscore, its estimate of how
plausible a pose is, NOT by affinity. "The affinity of the first pose" is the affinity of the
pose GNINA believes most; "the best affinity of all poses" rewards implausible poses that happen
to score well. This script takes the best (lowest) affinity among the ``--top-poses`` highest
CNN-ranked poses (default 3), a common compromise. ``--top-poses 1`` keeps GNINA's own first
pose. With ``--cnn-scoring none`` the poses are ordered by affinity and the choice is moot.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from rdkit import Chem, RDLogger
from rdkit.Chem import AllChem

import deepmedchem as dmc

RDLogger.DisableLog("rdApp.*")


class LigandError(RuntimeError):
    """The molecule could not be turned into a 3D ligand; it is reported as failed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gnina_version(binary: str) -> str:
    """The exact GNINA build, recorded with the scores so runs stay comparable."""

    try:
        finished = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=60, check=False
        )
    except FileNotFoundError as error:
        raise SystemExit(
            f"GNINA binary {binary!r} not found; install it or set GNINA_PATH"
        ) from error
    output = (finished.stdout or finished.stderr).strip()
    if finished.returncode != 0 or not output:
        raise SystemExit(f"`{binary} --version` failed: {output[:300]}")
    return output.splitlines()[0]


def protonate(smiles: str, ph: float) -> str:
    """Protonate for a pH with Open Babel; any other tool (Dimorphite-DL, Epik...) works too."""

    obabel = shutil.which("obabel")
    if obabel is None:
        raise SystemExit("--ph needs Open Babel (`obabel` on PATH); omit --ph to skip protonation")
    finished = subprocess.run(
        [obabel, f"-:{smiles}", "-osmi", "-p", str(ph)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    result = finished.stdout.split()
    if finished.returncode != 0 or not result:
        raise LigandError("protonation failed")
    return result[0]


def prepare_ligand(smiles: str, path: Path, *, seed: int, ph: float | None) -> None:
    """Minimal ligand preparation: (protonate), add hydrogens, embed one 3D conformer, write SDF."""

    if ph is not None:
        smiles = protonate(smiles, ph)
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise LigandError("RDKit could not parse the SMILES")
    molecule = Chem.AddHs(molecule)
    parameters = AllChem.ETKDGv3()
    parameters.randomSeed = seed
    if AllChem.EmbedMolecule(molecule, parameters) != 0:
        parameters.useRandomCoords = True
        if AllChem.EmbedMolecule(molecule, parameters) != 0:
            raise LigandError("3D embedding failed")
    if AllChem.MMFFHasAllMoleculeParams(molecule):
        AllChem.MMFFOptimizeMolecule(molecule, maxIters=500)
    writer = Chem.SDWriter(str(path))
    writer.write(molecule)
    writer.close()


def _float(molecule, name: str) -> float | None:
    if molecule is None or not molecule.HasProp(name):
        return None
    try:
        return float(molecule.GetProp(name))
    except ValueError:
        return None


def select_pose(poses: list[dict[str, Any]], top_poses: int) -> dict[str, Any] | None:
    """Best (lowest) affinity among the first ``top_poses`` poses in GNINA's own order."""

    candidates = [pose for pose in poses[: max(1, top_poses)] if pose["affinity"] is not None]
    return min(candidates, key=lambda pose: pose["affinity"]) if candidates else None


class GninaScorer:
    """Turns a list of SMILES into one docking score each, docking several molecules at once."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.binary = args.gnina
        self.receptor = args.receptor.resolve()
        self.box = self._box_arguments()

    def _box_arguments(self) -> list[str]:
        args = self.args
        if args.autobox_ligand is not None:
            return [
                "--autobox_ligand",
                str(args.autobox_ligand.resolve()),
                "--autobox_add",
                str(args.autobox_add),
            ]
        center, size = args.center, args.size
        return [
            *(
                "--center_x",
                str(center[0]),
                "--center_y",
                str(center[1]),
                "--center_z",
                str(center[2]),
            ),
            *("--size_x", str(size[0]), "--size_y", str(size[1]), "--size_z", str(size[2])),
        ]

    def identity(self) -> dict[str, Any]:
        """What produced the scores. Pinned at creation: a changed setup is refused later."""

        args = self.args
        box: dict[str, Any]
        if args.autobox_ligand is not None:
            box = {
                "autobox_ligand_sha256": sha256_file(args.autobox_ligand),
                "autobox_add": args.autobox_add,
            }
        else:
            box = {"center": list(args.center), "size": list(args.size)}
        return {
            "name": "gnina",
            "version": gnina_version(self.binary),
            "target": args.target or self.receptor.stem,
            "receptor_sha256": sha256_file(self.receptor),
            "box": box,
            "settings": {
                "exhaustiveness": args.exhaustiveness,
                "num_modes": args.num_modes,
                "cnn_scoring": args.cnn_scoring,
                "pose_selection": f"best affinity among top {args.top_poses} GNINA-ranked poses",
                "ph": args.ph,
                "seed": args.seed,
                "ligand_prep": "rdkit-etkdgv3-mmff",
            },
        }

    def dock_one(self, index: int, smiles: str, work: Path) -> dict[str, Any] | None:
        """Dock one molecule. Any failure is one failed row, never a failed batch."""

        args = self.args
        ligand = work / f"ligand_{index}.sdf"
        poses_path = work / f"poses_{index}.sdf"
        try:
            prepare_ligand(smiles, ligand, seed=args.seed, ph=args.ph)
        except LigandError as error:
            return {"score": None, "status": "failed", "error": str(error)}
        command = [
            self.binary,
            "-r", str(self.receptor),
            "-l", str(ligand),
            *self.box,
            "-o", str(poses_path),
            "--num_modes", str(args.num_modes),
            "--exhaustiveness", str(args.exhaustiveness),
            "--cpu", str(args.cpu),
            "--seed", str(args.seed),
            "--cnn_scoring", args.cnn_scoring,
            "-q",
        ]  # fmt: skip
        try:
            finished = subprocess.run(
                command, capture_output=True, text=True, timeout=args.timeout, check=False
            )
        except subprocess.TimeoutExpired:
            return {"score": None, "status": "timeout", "error": f"gnina exceeded {args.timeout} s"}
        if finished.returncode != 0 or not poses_path.is_file():
            message = (finished.stderr or finished.stdout).strip().splitlines()
            return {
                "score": None,
                "status": "failed",
                "error": f"gnina exit {finished.returncode}: {' '.join(message[-2:])[:300]}",
            }
        poses = [
            {
                "rank": rank,
                "affinity": _float(pose, "minimizedAffinity"),
                "cnn_score": _float(pose, "CNNscore"),
                "cnn_affinity": _float(pose, "CNNaffinity"),
            }
            for rank, pose in enumerate(Chem.SDMolSupplier(str(poses_path), sanitize=False), 1)
        ]
        chosen = select_pose(poses, args.top_poses)
        if chosen is None:
            return {"score": None, "status": "failed", "error": "no pose with an affinity"}
        if args.keep_poses is not None:
            args.keep_poses.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha1(smiles.encode()).hexdigest()[:12]
            shutil.copy(poses_path, args.keep_poses / f"{digest}.sdf")
        return {
            "score": chosen["affinity"],  # kcal/mol, lower is better
            "pose_rank": chosen["rank"],  # metrics: stored with the results, not optimized
            "cnn_score": chosen["cnn_score"],
            "cnn_affinity": chosen["cnn_affinity"],
            "poses": len(poses),
        }

    def __call__(self, smiles: list[str]) -> list[dict[str, Any] | None]:
        """The scorer Navigator calls: one value per SMILES, in order."""

        with tempfile.TemporaryDirectory(prefix="gnina-batch-") as directory:
            work = Path(directory)
            with ThreadPoolExecutor(max_workers=self.args.workers) as pool:
                futures = [
                    pool.submit(self.dock_one, index, text, work)
                    for index, text in enumerate(smiles)
                ]
                return [future.result() for future in futures]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter
    )
    target = parser.add_argument_group("target")
    target.add_argument("--receptor", type=Path, required=True, help="Prepared receptor PDB")
    target.add_argument("--autobox-ligand", type=Path, help="Reference ligand defining the box")
    target.add_argument("--autobox-add", type=float, default=4.0, help="Box padding in A")
    target.add_argument("--center", type=float, nargs=3, metavar=("X", "Y", "Z"))
    target.add_argument("--size", type=float, nargs=3, metavar=("X", "Y", "Z"))
    target.add_argument("--target", help="Target label recorded with the scores")

    docking = parser.add_argument_group("docking")
    docking.add_argument("--gnina", default=os.environ.get("GNINA_PATH", "gnina"))
    docking.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) // 4),
                         help="GNINA processes run in parallel (default: CPUs / 4)")  # fmt: skip
    docking.add_argument("--cpu", type=int, default=4, help="CPUs per GNINA process")
    docking.add_argument("--exhaustiveness", type=int, default=8)
    docking.add_argument("--num-modes", type=int, default=9)
    docking.add_argument("--top-poses", type=int, default=3, help="See 'Pose selection' above")
    docking.add_argument(
        "--cnn-scoring", default="rescore", choices=("none", "rescore", "refinement", "all")
    )
    docking.add_argument("--ph", type=float, help="Protonate for this pH with Open Babel")
    docking.add_argument("--seed", type=int, default=0)
    docking.add_argument("--timeout", type=float, default=900, help="Seconds per molecule")
    docking.add_argument("--keep-poses", type=Path, help="Copy every pose file into this folder")

    campaign = parser.add_argument_group("optimization")
    campaign.add_argument("--name", default="gnina-docking", help="Optimization name (resume key)")
    campaign.add_argument("--database", default="enamine")
    campaign.add_argument("--budget", type=int, default=1000)
    campaign.add_argument("--batch-size", type=int, default=100)
    campaign.add_argument("--strategy", help="Navigator strategy (default: server default)")
    campaign.add_argument("--no-druglike", dest="druglike", action="store_false",
                          help="Do not apply the druglike filter")  # fmt: skip
    campaign.add_argument("--output", default="gnina_results.csv")
    args = parser.parse_args()
    if args.autobox_ligand is None and (args.center is None or args.size is None):
        parser.error("give --autobox-ligand, or both --center and --size")
    if args.autobox_ligand is not None and (args.center is not None or args.size is not None):
        parser.error("--autobox-ligand and --center/--size are alternatives")
    return args


def main() -> None:
    args = parse_args()
    scorer = GninaScorer(args)
    identity = scorer.identity()  # also checks that gnina runs before anything is created
    print(
        f"Docking with {identity['version']} against {args.receptor} "
        f"({args.workers} x {args.cpu} CPUs)"
    )

    result = dmc.optimize(
        scorer,
        direction="minimize",  # docking scores: lower is better; scores are never negated
        database=args.database,
        budget=args.budget,
        batch_size=args.batch_size,
        name=args.name,
        strategy=args.strategy,
        filters="druglike" if args.druglike else None,
        scorer=identity,
    )

    print(f"\nBest of {len(result)} docked molecules (kcal/mol):")
    for observation in result.top(10):
        cnn = observation.metrics.get("cnn_score")
        cnn_text = "-" if cnn is None else f"{cnn:.2f}"
        print(f"{observation.score:7.2f}  CNNscore {cnn_text}  {observation.smiles}")
    result.to_csv(args.output)
    print(f"\nWrote {len(result)} rows to {args.output}")


if __name__ == "__main__":
    main()
