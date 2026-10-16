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
| Required/excluded structures | `constraints={"structures": {"all": [...], "none": [...]}}`; check `catalog()["optimization"]["constraints"]["enabled"]` |
| Exact windows on RDKit descriptors | `properties={"MolWt": {"max": 400}, ...}`; the offered names are in `catalog()["optimization"]["properties"]` |
| The druglike gate | `filters="druglike"` |
| Which space is searched | `database=`, plus the strategy and the optional `seed` |

Put hard requirements in call parameters, not in the callback as zero/penalty scores. Docking
and proprietary models remain custom scores. A requirement unsupported by the optimization
catalog cannot be made reliable by hiding it in the score. Current capabilities:

- *"minimize logP, stay under 400 Da"* — fully supported, and logP can be both a window and the
  objective ([`06_hard_limits_soft_score.py`](06_hard_limits_soft_score.py)).
- *"only molecules containing this substructure"* — supported on deployments advertising
  `optimization.constraints.enabled`. Required, excluded, and thresholded `any` groups use the
  same typed SMILES/SMARTS queries or versioned patterns as selections. Every proposed product
  is checked before custom scoring. This first implementation reports `post_assembly_only`:
  rare motifs can still produce short or empty batches after bounded refill. It does **not**
  yet restrict synthon generation. For rare motifs, use the substructure-first workflow in
  [`07_motif_focused_screening.py`](07_motif_focused_screening.py).
- *"ADMET below a threshold"* — optimization does not yet advertise this capability. Do not
  replace it with a zero-valued callback penalty or interpret CP16 acquisition scores as
  assembled-product model predictions. Built-in ADMET evaluation is a later integration step.
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

## Constrained docking

The callback computes docking; eligibility is declared separately:

```python
result = dmc.optimize(
    docking_score,
    direction="minimize",
    database="enamine",
    budget=2000,
    batch_size=100,
    properties={"MolWt": (None, 400)},
    constraints={"structures": {
        "all": [{"pattern_id": "alpha-amino-acid/v1"}],
        "none": [{"query": {"format": "smarts", "value": "[N+](=O)[O-]"}}],
    }},
)
```

`docking_score` is your function; the service does not execute docking. The named amino-acid
pattern has a specific chemical definition; use an explicit query when you need different
protection, substitution, or stereochemical semantics. Structural matching uses chirality.
Counts refer to unique atom matches, including overlapping matches; bounds are limited to 1000.

Typed descriptor conditions also work in `constraints.properties`:
`{"property_id": "rdkit.mol_wt", "operator": "lte", "value": 400, "units": "Da"}`.
They intersect with `properties=` and `filters=`; contradictions are errors. RDKit is optional
on the client. The server validates registry IDs and the worker compiles chemistry queries
before proposing. Unsupported constraints fail instead of falling back to a callback.

For the CLI, save the constraints object in `constraints.json` and use:

```bash
dmc optimize run constrained-docking -d enamine --minimize --property MolWt=:400 \
    --constraints constraints.json --score-cmd './dock.sh {input} {output}'
```

The constraint specification is part of resume identity. Changing it requires a new run name.
Internal rejections do not consume the external scoring budget. Seed evidence remains audited
under the existing seed policy; imported measured seeds are not newly proposed molecules.
Built-in descriptor objectives and mixed objectives are not implemented by this constraint
release: the earlier property-objective examples remain compatibility examples for that reason.
