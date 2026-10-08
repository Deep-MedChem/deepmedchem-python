"""Score a Navigator batch with Schrödinger LigPrep + Glide.

This is a *score command*: it reads the ``id,smiles`` CSV that ``dmc optimize`` writes and writes
the ``id,score,status,error,...`` CSV it reads back. Use it with the CLI loop::

    dmc optimize run kif11-glide -d enamine --minimize --budget 2000 --batch-size 200 \\
        --score-cmd 'python glide_score.py {input} {output} --grid kif11_grid.zip'

or, on a batch queue, with ``dmc optimize ask`` / ``tell`` (see ``run.sh``).

Requirements: a Schrödinger installation with a Glide licence (``$SCHRODINGER`` set) and a
receptor grid (``.zip``) prepared with the Receptor Grid Generation panel or ``glide`` itself.
Only the standard library is used here, so any Python 3.9+ runs it.

Scoring choices, stated so the numbers can be compared across runs:

* LigPrep enumerates ionization/tautomer/stereo variants (``--max-variants``); a molecule's score
  is the best (lowest) Glide docking score over its variants and poses.
* ``r_i_docking_score`` is the optimized number (kcal/mol-like, lower is better, so the
  optimization must be created with ``--minimize``). Emodel and the variant count are returned
  as metrics, never optimized.
* A molecule LigPrep rejects or Glide cannot place is reported as ``failed`` with a reason, never
  dropped: Navigator learns that it is not worth proposing its neighbours.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCORE = "r_i_docking_score"
EMODEL = "r_i_glide_emodel"
NO_POSE = "no Glide pose (LigPrep or docking)"


def schrodinger_tool(name: str) -> str:
    root = os.environ.get("SCHRODINGER")
    if not root:
        sys.exit("SCHRODINGER is not set; point it at your Schrödinger installation.")
    tool = Path(root) / name
    if not tool.exists():
        sys.exit(f"{tool} not found; check the SCHRODINGER installation.")
    return str(tool)


def read_batch(path: Path) -> list[tuple[str, str]]:
    with path.open(newline="") as handle:
        return [(row["id"], row["smiles"]) for row in csv.DictReader(handle)]


def run(command: list[str], cwd: Path, log: Path) -> None:
    with log.open("w") as out:
        completed = subprocess.run(command, cwd=cwd, stdout=out, stderr=subprocess.STDOUT)
    if completed.returncode != 0:
        tail = log.read_text(errors="replace")[-2000:]
        sys.exit(f"{Path(command[0]).name} failed (exit {completed.returncode}):\n{tail}")


def ligprep(molecules: list[tuple[str, str]], work: Path, args: argparse.Namespace) -> Path:
    # Navigator ids contain no whitespace, so they are safe SMILES titles; LigPrep keeps the
    # title on every variant it generates, which is how scores are mapped back.
    smi = work / "batch.smi"
    smi.write_text("".join(f"{smiles} {mol_id}\n" for mol_id, smiles in molecules))
    output = work / "ligprep.maegz"
    command = [
        schrodinger_tool("ligprep"),
        "-ismi", smi.name,
        "-omae", output.name,
        "-ph", str(args.ph),
        "-s", str(args.max_variants),
        "-NJOBS", str(args.jobs),
        "-HOST", f"localhost:{args.jobs}",
        "-WAIT",
    ]
    if args.epik:
        command.append("-epik")
    run(command, work, work / "ligprep.log")
    if not output.exists():
        sys.exit("LigPrep finished without writing ligprep.maegz; see ligprep.log.")
    return output


def glide(ligands: Path, work: Path, args: argparse.Namespace) -> Path:
    job = "glide_dock"
    (work / f"{job}.in").write_text(
        "\n".join(
            [
                f"GRIDFILE {Path(args.grid).resolve()}",
                f"LIGANDFILE {ligands.name}",
                f"PRECISION {args.precision}",
                "POSES_PER_LIG 1",
                "POSE_OUTTYPE ligandlib_sd",
                "COMPRESS_POSES False",
                "",
            ]
        )
    )
    run(
        [
            schrodinger_tool("glide"),
            f"{job}.in",
            "-NJOBS", str(args.jobs),
            "-HOST", f"localhost:{args.jobs}",
            "-WAIT",
        ],
        work,
        work / "glide.log",
    )
    return work / job


def parse_number(value: str | None) -> float | None:
    try:
        number = float(value) if value not in (None, "") else None
    except ValueError:
        return None
    return number if number is not None and math.isfinite(number) else None


def poses_from_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def poses_from_sdf(path: Path) -> list[dict[str, str]]:
    """Read title and Glide properties from an SD file (optionally gzipped) without RDKit."""
    poses: list[dict[str, str]] = []
    record: dict[str, str] = {}
    raw = path.read_bytes()
    if path.suffix == ".sdfgz":
        raw = gzip.decompress(raw)
    lines = raw.decode(errors="replace").splitlines()
    index = 0
    title_next = True
    while index < len(lines):
        line = lines[index]
        if title_next:
            record = {"title": line.strip()}
            title_next = False
        elif line.startswith(">") and "<" in line:
            key = line[line.index("<") + 1 : line.rindex(">")]
            index += 1
            record[key] = lines[index].strip() if index < len(lines) else ""
        elif line.strip() == "$$$$":
            poses.append(record)
            title_next = True
        index += 1
    return poses


def collect_poses(job: Path) -> list[dict[str, str]]:
    report = job.with_suffix(".csv")
    if report.exists():
        return poses_from_csv(report)
    # Glide writes a <job>.csv report; the pose library is the fallback.
    for sdf in (job.parent / f"{job.name}_lib.sdf", job.parent / f"{job.name}_lib.sdfgz"):
        if sdf.exists():
            return poses_from_sdf(sdf)
    return []


def best_per_molecule(poses: list[dict[str, str]]) -> dict[str, dict[str, float | int]]:
    best: dict[str, dict[str, float | int]] = {}
    for pose in poses:
        title = (pose.get("title") or pose.get("s_m_title") or "").strip()
        score = parse_number(pose.get(SCORE))
        if not title or score is None:
            continue
        entry = best.setdefault(title, {"score": score, "variants": 0})
        entry["variants"] = int(entry["variants"]) + 1
        if score <= float(entry["score"]):
            entry["score"] = score
            emodel = parse_number(pose.get(EMODEL))
            if emodel is not None:
                entry["glide_emodel"] = emodel
    return best


def write_scores(
    output: Path,
    molecules: list[tuple[str, str]],
    best: dict[str, dict[str, float | int]],
) -> tuple[int, int]:
    valid = 0
    with output.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "score", "status", "error", "glide_emodel", "variants_docked"])
        for mol_id, _ in molecules:
            entry = best.get(mol_id)
            if entry is None:
                writer.writerow([mol_id, "", "failed", NO_POSE, "", ""])
                continue
            valid += 1
            writer.writerow(
                [
                    mol_id,
                    f"{float(entry['score']):.4f}",
                    "valid",
                    "",
                    entry.get("glide_emodel", ""),
                    entry["variants"],
                ]
            )
    return valid, len(molecules) - valid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("input", type=Path, help="batch CSV with id,smiles columns")
    parser.add_argument("output", type=Path, help="scores CSV to write")
    parser.add_argument("--grid", required=True, help="Glide receptor grid (.zip)")
    parser.add_argument("--precision", choices=["HTVS", "SP", "XP"], default="SP")
    parser.add_argument("--jobs", type=int, default=4, help="parallel LigPrep/Glide subjobs")
    parser.add_argument("--ph", type=float, default=7.4)
    parser.add_argument("--max-variants", type=int, default=4, help="LigPrep stereoisomers")
    parser.add_argument("--no-epik", dest="epik", action="store_false")
    parser.add_argument("--keep", type=Path, help="keep the Schrödinger work files here")
    args = parser.parse_args()

    if not Path(args.grid).exists():
        sys.exit(f"grid file not found: {args.grid}")
    molecules = read_batch(args.input)
    work = Path(tempfile.mkdtemp(prefix="glide-batch-"))
    try:
        ligands = ligprep(molecules, work, args)
        job = glide(ligands, work, args)
        valid, failed = write_scores(args.output, molecules, best_per_molecule(collect_poses(job)))
        print(f"glide: {valid} scored, {failed} failed", file=sys.stderr)
    finally:
        if args.keep:
            shutil.copytree(work, args.keep, dirs_exist_ok=True)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
