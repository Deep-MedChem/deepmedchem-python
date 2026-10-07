# Optimization examples

Navigator proposes molecules from a make-on-demand chemical space; **your** scorer scores them on
your machine; the scores go back and steer the next proposals until the budget is spent. Only
SMILES and numbers cross the network.

| File | Needs | Shows |
| --- | --- | --- |
| [`01_property_logp_qed.py`](01_property_logp_qed.py) | `rdkit` | A first run in about a minute: a desirability score from QED and logP, metrics side channel |
| [`02_your_ml_model.py`](02_your_ml_model.py) | `rdkit`, `scikit-learn` | Morgan fingerprints into any `.predict()`; uncertainty and nearest-training similarity as metrics. The bundled training labels are **made up** placeholders |
| [`03_gnina_docking.py`](03_gnina_docking.py) | `rdkit`, the `gnina` binary | Ligand prep, parallel docking, explicit pose selection, per-molecule failures, `scorer=` identity, resume |
| [`04_glide/`](04_glide/) | Schrödinger LigPrep + Glide licence | A score *command* for `dmc optimize run --score-cmd`, best docking score over LigPrep variants, plus the `ask`/`tell` batch-queue loop |
| [`../docs/optimize.py`](../docs/optimize.py) | nothing | A pure-Python toy scorer, run in CI |

All of them need `pip install deepmedchem` and a key (`dmc login` or `DEEPMEDCHEM_API_KEY`).

## The scorer contract

A scorer is a function that takes `list[str]` of SMILES and returns one value per SMILES, in order:

- a number: a valid score;
- `None`, NaN or infinity: a molecule that could not be scored (reported as failed, never dropped);
- a dict `{"score": x, "status": ..., "error": ..., **metrics}`: extra keys are stored as metrics
  (numbers, strings, bools or None, at most 32) and returned with the results, never optimized.

If the scorer raises, nothing is submitted and the batch stays pending. `direction` is required:
docking scores are minimized, most model predictions maximized, and scores are never negated.

## Resuming

`name=` (or the NAME of `dmc optimize run`) is the resume key. Re-running the same call after
Ctrl-C, a crash or a reboot continues the same optimization. Scores are written to a local
journal (`platformdirs.user_cache_dir("deepmedchem")/optimizations/`, or `DEEPMEDCHEM_CACHE_DIR`)
before they are uploaded, so a lost upload is re-sent instead of re-scored.

## Scorers that are programs, not Python

Any program that reads an `id,smiles` CSV and writes an `id,score[,status,error,...]` CSV works:

```bash
dmc optimize run my-campaign -d enamine --minimize --budget 2000 --batch-size 200 \
    --score-cmd './dock.sh {input} {output}'
```

For batch queues (SLURM, LSF, PBS), split the loop:

```bash
dmc optimize ask my-campaign -o batch.csv        # exit 0: written, 3: not ready yet, 4: finished
sbatch --wait score.sh batch.csv scores.csv
dmc optimize tell my-campaign scores.csv
dmc optimize results my-campaign --top 50 -o best.csv
```
