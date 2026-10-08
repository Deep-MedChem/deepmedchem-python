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
| [`05_seed_and_harvest.py`](05_seed_and_harvest.py) | nothing | Measured in-space seeds, then analog harvesting around them |
| [`06_hard_limits_soft_score.py`](06_hard_limits_soft_score.py) | `rdkit` | Hard descriptor windows the service enforces vs. the soft preference you score; the same descriptor used both ways |
| [`07_motif_focused_screening.py`](07_motif_focused_screening.py) | `rdkit` | What to do when a substructure is a **requirement**: SMARTS search, then local ranking |
| [`../docs/optimize.py`](../docs/optimize.py) | nothing | A pure-Python toy scorer, run in CI |

All of them need `pip install deepmedchem` and a key (`dmc login` or `DEEPMEDCHEM_API_KEY`).

## What you can constrain, and what you can only score

The objective is yours: any scalar your machine can compute — a model prediction, a docking
score, a multi-objective desirability — so "maximize predicted activity for my target" is a
matter of plugging your model in ([`02_your_ml_model.py`](02_your_ml_model.py)).

What the **service** enforces on the molecules it proposes is a shorter list:

| Constraint | How |
| --- | --- |
| Exact windows on RDKit descriptors | `properties={"MolWt": {"max": 400}, ...}`; the offered names are in `catalog()["optimization"]["properties"]` |
| The druglike gate | `filters="druglike"` |
| Which space is searched | `database=`, plus the strategy and the optional `seed` |

Anything else is a preference you express through the score, and a score cannot exclude: it ranks
what was proposed. That distinction decides whether a request is expressible today:

- *"minimize logP, stay under 400 Da"* — fully supported, and logP can be both a window and the
  objective ([`06_hard_limits_soft_score.py`](06_hard_limits_soft_score.py)).
- *"only molecules containing this substructure"* — **not supported.** There are no SMARTS
  constraints in the optimization gate, and rewarding the motif in the score does not work:
  a specific motif is so rare that the surrogate never sees a positive example (measured: a
  150-molecule run whose score was dominated by an acrylamide reward returned 0 matches). Use
  [`07_motif_focused_screening.py`](07_motif_focused_screening.py) instead.
- *"scaffold-hop from this molecule of mine"* — **not supported end to end.** You can put
  similarity to your reference in the score, but the search cannot be restricted to that
  neighbourhood, and the same needle-in-a-haystack problem applies. Seeding does not bridge it:
  `mode="external"` SMILES rows train the surrogate only — they explicitly do not create
  analog-search starting points — and there is no decomposition of an arbitrary SMILES into
  synthons. Analog harvesting needs seeds that are already in the space, identified by Navigator
  `product_id` ([`05_seed_and_harvest.py`](05_seed_and_harvest.py)).

Substructure hits cannot be reused as seeds: their `product_id` comes from the platform search,
which is a different identifier space (and a different release lineage) from the Navigator space
the optimization worker runs, so seeding rejects those ids as malformed.

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
