# Optimization examples

Use explicit constraints to guide candidate generation and built-in objectives for supported
chemistry. Supply a callback only for an external calculation, such as docking or your model.
Check `dmc.catalog()["optimization"]` before starting a run on a deployment.

| File | Needs | Shows |
| --- | --- | --- |
| [`01_property_logp_qed.py`](01_property_logp_qed.py) | nothing | Internal QED objective with a logP constraint |
| [`02_your_ml_model.py`](02_your_ml_model.py) | `rdkit`, `scikit-learn` | Morgan fingerprints into any `.predict()`; uncertainty and nearest-training similarity as metrics. The bundled training labels are **made up** placeholders |
| [`03_gnina_docking.py`](03_gnina_docking.py) | `rdkit`, the `gnina` binary | Ligand prep, parallel docking, explicit pose selection, per-molecule failures, `scorer=` identity, resume |
| [`04_glide/`](04_glide/) | Schrödinger LigPrep + Glide licence | A score *command* for `dmc optimize run --score-cmd`, best docking score over LigPrep variants, plus the `ask`/`tell` batch-queue loop |
| [`05_seed_and_harvest.py`](05_seed_and_harvest.py) | nothing | Measured in-space seeds, then analog harvesting around them |
| [`06_hard_limits_soft_score.py`](06_hard_limits_soft_score.py) | nothing | Hard descriptor windows and normalized internal preferences |
| [`07_motif_focused_screening.py`](07_motif_focused_screening.py) | `rdkit` | What to do when a substructure is a **requirement**: guided optimization, or direct SMARTS search |
| [`../docs/optimize.py`](../docs/optimize.py) | nothing | A pure-Python toy scorer, run in CI |

All of them need `pip install deepmedchem` and a key (`dmc login` or `DEEPMEDCHEM_API_KEY`).

## What you can constrain, and what you can only score

| Chemistry | Explicit parameter | Execution |
| --- | --- | --- |
| Required/excluded motifs | `RequireStructure` / `ExcludeStructure` | Restricted synthon tuple branches, then exact product matching |
| Descriptor bounds | `PropertyRange` | Released additive descriptors and calibrated reaction margins, then exact RDKit checks |
| ADMET bounds | `PredictedPropertyRange` | CP16 acquisition, then pinned assembled-product teacher verification |
| Descriptor preferences | `Maximize` / `Minimize` with a property ID | Internal acquisition and exact scoring |
| Similarity to a molecule | `Maximize(Similarity(...), scale=...)` | Morgan-OR tuple ranking and exact product ECFP4 Tanimoto |
| ADMET preferences | `Minimize(PredictedProperty(...), scale=...)` | CP16 ranking and pinned product-model scoring |
| Docking / custom model | callback, plus an `ExternalScore` term when mixing objectives | Local black-box scoring |

The default is guided acquisition. Exact checks are the final safeguard, not the candidate
generator. Anchor plans include intact motifs and junction pieces; stable exclusions can
remove synthons. Complex joins outside the bounded anchor planner and disconnected queries
retain unrestricted routes to avoid losing valid products. Missing additive assets cause an
explicit exact-check fallback in `engine.constraint_acquisition`. Search is heuristic, not an
exhaustive enumeration or a guarantee of uniform sampling within the constrained space.

ADMET requires both release-aligned CP16 factors and pinned product teachers. Missing endpoints,
wrong units or unavailable models fail explicitly. CP16 estimates never certify a hard bound;
non-finite teacher predictions are rejected. These values are predictions, not measurements.

Substructure hits cannot be reused as seeds: their `product_id` comes from the platform search,
which is a different identifier space (and a different release lineage) from the Navigator space
the optimization worker runs, so seeding rejects those ids as malformed.

## The scorer contract

A scorer is a function that takes `list[str]` of SMILES and returns one value per SMILES, in order:

- a number: a valid score;
- `None`, NaN or infinity: a molecule that could not be scored (reported as failed, never dropped);
- a dict `{"score": x, "status": ..., "error": ..., **metrics}`: extra keys are stored as metrics
  (numbers, strings, bools or None, at most 32) and returned with the results, never optimized.

If the scorer raises, nothing is submitted and the batch stays pending. Declare `direction="minimize"`
for docking. Legacy external scores stay in their original units; explicit objective terms use
the normalization below and the resulting aggregate is maximized.

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
    constraints=[
        dmc.PropertyRange("MolWt", max=400),
        dmc.RequireStructure("alpha-amino-acid/v1"),
        dmc.ExcludeStructure(smarts="[N+](=O)[O-]"),
    ],
)
```

`docking_score` is your function; the service does not execute docking. The named amino-acid
pattern has a specific chemical definition; use an explicit query when you need different
protection, substitution, or stereochemical semantics. Structural matching uses chirality.
Counts refer to unique atom matches, including overlapping matches; bounds are limited to 1000.

`PropertyRange("MolWt", max=400)` also accepts `"mw"` or `"rdkit.mol_wt"`; known units
are inferred. All constraints are conjoined. Use `RequireAnyStructure([...], minimum=1)`
for alternatives (one group per specification). Existing structured constraint dictionaries
and `properties=` mappings still work; they intersect with `filters=` and typed constraints.
Contradictions are errors. RDKit is optional
on the client. The server validates registry IDs and the worker compiles chemistry queries
before proposing. Unsupported constraints fail instead of falling back to a callback.

The CLI can express the same common constraints directly:

```bash
dmc optimize run constrained-docking -d enamine --minimize --property MolWt=:400 \
    --require-pattern alpha-amino-acid/v1 --exclude-smarts '[N+](=O)[O-]' \
    --score-cmd './dock.sh {input} {output}'
```

`--require-pattern`, `--exclude-pattern`, `--require-smarts` and `--exclude-smarts` are
repeatable. They append to conditions from an optional `--constraints constraints.json`;
the combined request is validated before submission. JSON remains useful for ADMET and
alternative structure groups. `ExcludeStructure` always rejects at least `min_count` matches
(default 1, must be positive); it rejects `max_count` to avoid letting more frequent motifs pass.

The constraint specification is part of resume identity. Changing it requires a new run name.
Internal rejections do not consume the external scoring budget. Seed evidence remains audited
under the existing seed policy; imported measured seeds are not newly proposed molecules.

## Built-in and mixed objectives

Use `Maximize` or `Minimize` with `scale=(lower, upper)` and an optional
positive `weight` (default 1). Named `Scale(lower=..., upper=...)` objects also work.
The service clips `(value - lower) / (upper - lower)` to [0, 1], reverses it for `minimize`, and
maximizes the weighted arithmetic mean. Thus units are never added directly. Bounds on an
objective normalize a preference; use constraints to enforce eligibility.

```python
result = dmc.optimize(
    docking_score, database="enamine", budget=500, batch_size=50,
    constraints=[dmc.PropertyRange("MolWt", max=400)],
    objectives=[
        dmc.Minimize(dmc.ExternalScore(), scale=(-12, -4), weight=2),
        dmc.Maximize("rdkit.qed", scale=(0, 1)),
    ],
)
```

The callback returns raw docking values only. The external surrogate learns its normalized
component separately from the known chemistry. Results contain the aggregate `score`, raw
`metrics.objective_0`, `metrics.objective_1`, etc., and `objective_N_model` for ADMET terms.
Those metric names are reserved for internal provenance. For imported seeds in a mixed run,
provide raw external scores; the engine computes the internal components before ingestion.

For similarity use a term such as
`dmc.Maximize(dmc.Similarity("CCO"), scale=(0, 1))` alongside a structure constraint. This guides ranking inside the
eligible tuple branches. It does not decompose an arbitrary reference into an in-space seed.

For an advertised ADMET endpoint use
`dmc.PredictedPropertyRange(endpoint, units=advertised_units, max=threshold)` in `constraints`,
or `dmc.Minimize(dmc.PredictedProperty(endpoint, units=advertised_units),
scale=(3, 8))` in `objectives`. Use model-appropriate units and thresholds.

A built-in-only run omits the callback and finishes on the worker. Its budget counts internally
scored products; rejected candidates do not consume that molecule budget. Hosted deployments
with paid optimization reserve the complete built-in-only budget at creation; external/mixed
runs retain per-round reservations. Pause, cancel, resume and result retrieval still work.

Typed objects work identically with `dmc.optimize`, `client.optimizations.create` and
`await async_client.optimizations.create`. Built-in and mixed objectives infer aggregate
`direction="maximize"`; a standalone callback still requires an explicit direction.
When migrating a saved campaign, keep its original request or use `dmc.resume(name)`:
typed serialization can differ from a legacy request, so use a new name for a converted spec.
