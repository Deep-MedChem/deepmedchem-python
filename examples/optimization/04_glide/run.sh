#!/usr/bin/env bash
# Optimize a Glide docking score with Navigator.
#
# Needs: pip install deepmedchem; `dmc login` (or DEEPMEDCHEM_API_KEY); $SCHRODINGER with a
# Glide licence; a receptor grid. Glide docking scores are lower-is-better: --minimize.
set -euo pipefail

NAME=${NAME:-kif11-glide}
GRID=${GRID:-kif11_grid.zip}
HERE=$(cd "$(dirname "$0")" && pwd)

# A. One machine: dmc runs the loop and calls the scorer for every batch. Re-running the same
#    command after an interruption resumes; scores already computed are not recomputed.
dmc optimize run "$NAME" --database enamine --minimize --budget 2000 --batch-size 200 \
    --druglike --scorer '{"name": "glide", "precision": "SP", "grid": "'"$GRID"'"}' \
    --score-cmd "python $HERE/glide_score.py {input} {output} --grid $GRID --jobs 8"

# B. A batch queue (SLURM shown): fetch a batch, dock it as a job, send the scores back.
#    Run this from a login node or a cron job; each pass handles one round.
#
#    dmc optimize ask "$NAME" -o batch.csv --wait 600 || exit $?   # 3: not ready, 4: finished
#    sbatch --wait --cpus-per-task=16 --wrap \
#        "python $HERE/glide_score.py batch.csv scores.csv --grid $GRID --jobs 16"
#    dmc optimize tell "$NAME" scores.csv

dmc optimize results "$NAME" --top 50 -o "$NAME-best.csv"
