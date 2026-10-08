---
name: deepmedchem
description: >-
  Search make-on-demand chemical spaces (Enamine REAL, Freedom Space, ChemInfinita, Synple,
  VAST and others, billions to trillions of molecules) through the DeepMedChem platform with
  the `deepmedchem` Python package and its `dmc` CLI. Use when a task involves finding
  purchasable analogs or similar molecules for a SMILES (ECFP4/Morgan, 3D shape, or
  electrostatic similarity), exact SMILES/SMARTS substructure search, random sampling of a
  chemical space, exporting hits to CSV/SDF/SMILES, checking CHEESE Credits, building
  molecule-selection/1 documents or durable runs, preparing vendor quote and order
  requests, or optimizing a user's own score (docking such as GNINA or Glide, an ML model, an
  RDKit property) over a chemical space with Navigator ask/tell (`dmc.optimize`,
  `dmc optimize run`). Also use when the user mentions DeepMedChem, CHEESE, `dmc login`,
  `import deepmedchem`, or `DEEPMEDCHEM_API_KEY`.
license: MIT
compatibility: Requires Python 3.9+, `pip install deepmedchem`, and network access to api.deepmedchem.com with a CHEESE API key.
metadata:
  author: Deep MedChem
  version: "1.1"
  homepage: https://github.com/Deep-MedChem/deepmedchem-python
---

# DeepMedChem chemical-space search

`deepmedchem` is the official, chemistry-thin Python client for the hosted DeepMedChem
platform. It ships a `dmc` command (alias `deepmedchem`) and a small Python API. All
chemistry runs on the server: the package contains no RDKit, models, or databases.

Read [references/cli.md](references/cli.md) for every command and flag, and
[references/python-api.md](references/python-api.md) for the Python surface (clients, result
models, Selection/Run builders, export, ordering) when the recipes below are not enough.

## Setup

```bash
pip install deepmedchem            # add "deepmedchem[sdf]" for SDF export (installs RDKit)
dmc login                          # device login: prints a code + URL, stores the key in the OS keyring
dmc status --verify                # confirms the profile and that the API accepts the key
```

- In CI or scripts, set `DEEPMEDCHEM_API_KEY` instead of `dmc login`. Keys are created at
  https://cheese.deepmedchem.com. Never paste a key into code, logs, or chat output.
- `dmc login --no-browser` prints only the URL for SSH/containers; `--token-stdin` accepts a key from a pipe.
- Errors mentioning `missing_api_key` mean neither the keyring, the credentials file, nor the
  environment variable holds a key for the selected profile.

## Pick the operation

| Goal | CLI | Python |
| --- | --- | --- |
| Which databases exist, their size, order contact | `dmc databases` (`--json` for ids) | `dmc.catalog()` |
| Nearest neighbours by 2D fingerprint (default) | `dmc search SMILES -d DB -n N` | `dmc.search(smiles, database=DB, limit=N)` |
| 3D shape or electrostatic analogs | `dmc search SMILES -d DB -m shape` / `-m esp` | `method="shape"` / `method="esp"` |
| Exact substructure, SMARTS query (default) | `dmc substructure QUERY -d DB` | `dmc.substructure(query, database=DB)` |
| Exact substructure from a concrete SMILES | `dmc substructure SMILES -f smiles -d DB` | `format="smiles"` |
| Random molecules from a space | `dmc sample -d DB -n N --seed S` | `dmc.sample(database=DB, count=N, seed=S)` |
| Plan and remaining daily credits | `dmc usage` | `dmc.usage()` |
| Multi-constraint or multi-query work | see references | `Selection`, `Run`, `Client.runs` |
| Exact RDKit property filters, experimental ADMET acquisition | see references | `Selection.where`, `.require_preset`, `.acquire_predicted_property` |
| Ask vendors for quotes or orders | `dmc order results.csv --get-quote` | `prepare_order(...)` |
| Optimize the user's own score (docking, ML model, property) | `dmc optimize run NAME -d DB --minimize --score-cmd 'CMD {input} {output}'` | `dmc.optimize(score, direction=..., database=DB, name=...)` |

Database ids are strings such as `enamine-real-v5a` or `freedom-space-5`. Do not guess them:
list them with `dmc databases --json` or `dmc.catalog()["libraries"]` and use `database_id`.

## CLI recipes

```bash
dmc search "CC(=O)Oc1ccccc1C(=O)O" -d enamine-real-v5a -n 10            # ECFP4 Tanimoto
dmc search "CC(=O)Oc1ccccc1C(=O)O" -d enamine-real-v5a -m shape -o hits.csv
dmc substructure "[N;R0][N;R0]C(=O)" -d enamine-real-v5a -n 50 -o hydrazides.sdf
dmc substructure "CNC(=O)N1CCC1" -f smiles -d enamine-real-v5a -n 20
dmc sample -d freedom-space-5 -n 100 --seed 7 -o sample.smi
dmc search "c1ccncc1" -d enamine-real-v5a --json > raw.json              # full API payload
dmc order hits.csv --get-quote --no-open                                  # email.txt + molecules.csv per vendor
```

- `-o FILE` writes CSV, SDF, SMILES (`.smi`), or JSON, inferred from the suffix; `--format` overrides.
- Human tables are for reading. For machine consumption use `--json` or an export file, which
  keep `product_id`, database, release, score, price, and every other field.
- `--profile NAME` selects a configured profile; `DEEPMEDCHEM_PROFILE` does the same.

## Python recipes

```python
import deepmedchem as dmc

hits = dmc.search("CC(=O)Oc1ccccc1C(=O)O", database="enamine-real-v5a", method="shape", limit=10)
for hit in hits.hits:                      # typed Hit objects
    print(hit.rank, hit.score, hit.price, hit.product_id, hit.smiles)
list(hits)                                 # a SearchResult is also a sequence of SMILES
hits.to_csv("hits.csv")                    # or .to_sdf(), .to_file(path), .to_pandas()

subs = dmc.substructure("[N;R0]C(=O)[N;R0]", database="enamine-real-v5a", limit=100, timeout_seconds=60)
rand = dmc.sample(database="freedom-space-5", count=50, seed=1)
print(dmc.usage())                         # plan, limit, used, remaining, reset_at
```

Module-level functions open and close a client per call. For many calls, reuse one client:

```python
from deepmedchem import Client

with Client() as client:                   # api_key=..., profile=..., timeout=45.0
    a = client.search("CCO", database="enamine-real-v5a", limit=5)
    b = client.search_substructure("c1ccncc1", query_format="smarts", database="enamine-real-v5a")
```

`deepmedchem.aio` and `AsyncClient` provide the same operations with `await`.

## Optimization with the user's own scorer (Navigator ask/tell)

Navigator proposes a batch of molecules; the user's scorer scores it locally; the scores go back
and steer the next batch until `budget` molecules are scored. The SDK contains no scorer: write
the scoring function (or shell command) yourself.

```python
import deepmedchem as dmc

def score(smiles: list[str]) -> list:          # one value per SMILES, in order
    ...                                         # number = valid; None/NaN = failed;
                                                # {"score": x, **metrics} = valid + stored metrics
result = dmc.optimize(score, direction="minimize", database="enamine",
                      budget=1000, batch_size=100, name="kif11-gnina",
                      scorer={"name": "gnina", "version": "1.3.2", "target": "kif11"})
result.top(10); result.best; result.to_csv("results.csv")    # Observation: id, smiles, score, status, round, metrics
```

Manual control and the CLI:

```python
with dmc.Client() as client:
    opt = client.optimizations.create(database="enamine", direction="maximize",
                                      budget=500, batch_size=50, name="my-model")
    while (batch := opt.ask()) is not None:    # long-polls; None when finished
        opt.tell(batch, my_model(batch.smiles))   # list aligned with batch.smiles, or {id: score}
    opt.results().top(20)
```

```bash
dmc optimize run NAME -d enamine --minimize --budget 2000 --batch-size 200 \
    --score-cmd './dock.sh {input} {output}'   # {input}: id,smiles CSV -> {output}: id,score[,status,error,metric...]
dmc optimize ask NAME -o batch.csv             # HPC: exit 0 written, 3 not ready, 4 finished
dmc optimize tell NAME scores.csv
dmc optimize status [NAME] | results NAME --top 50 -o best.csv | cancel NAME | resume NAME
```

- **`direction` is required** (`"minimize"` for docking scores, usually `"maximize"` for model
  predictions). Scores are never negated; do not negate them yourself.
- **`name` is the resume key.** Re-running the same `dmc.optimize(...)` or `dmc optimize run`
  continues the optimization; scores computed but not uploaded are re-sent from a local journal.
  The same name with a different specification is a 409 `idempotency_conflict`.
- **Report failures, never drop them.** Every proposed id needs exactly one row; the SDK fills
  ids a scorer did not return as `failed`. If the scorer raises, nothing is submitted.
- One scalar is optimized. Put other numbers in metrics; combine objectives in the scorer.
- `filters="druglike"` and `properties={"MolWt": (None, 500)}` restrict the space;
  `strategy` picks a Navigator preset (`gamma_diversity_screening` default). Allowed databases,
  strategies, properties and limits are in `dmc.catalog()["optimization"]`.
- `scorer={...}` is pinned at creation; a later submission with a different scorer is refused
  (`scorer_changed`). `paused` (`optimization_paused`, usually `insufficient_credits`) needs
  `opt.resume()` / `dmc optimize resume NAME`.
- Long docking loops belong in `dmc optimize run` or a script, not in an interactive agent turn.

## Rules that keep results correct and cheap

- **One credit per call.** Every successful synchronous search, substructure, or sample costs
  one CHEESE Credit from a shared daily balance. Check `dmc usage` before loops and batch work
  into durable runs instead of thousands of single calls.
- **10 second synchronous budget.** Slow SMARTS (recursive, many wildcards) can time out; raise
  `timeout_seconds` on substructure, simplify the pattern, or move to a run.
- **SMILES vs SMARTS.** Pass concrete molecules as `smiles`; use `smarts` for atom lists,
  ring or charge constraints, and recursive expressions. The CLI defaults substructure queries
  to SMARTS.
- **Methods.** `morgan` is ECFP4 Tanimoto on 2D graphs; `shape` and `esp` are CHEESE 3D
  shape and electrostatic similarities, useful for scaffold hopping. Scores are metric
  specific and should not be compared across methods.
- **Prices are estimates** in whole US dollars for US delivery, `None` when a vendor publishes
  none. Binding quotes come from the vendor via `dmc order --get-quote`.
- **Ordering never sends anything.** `dmc order` and `prepare_order` only write `email.txt`
  and a price-free `molecules.csv` per vendor and open a local mail draft.
- **Predicted ADMET is not a measurement.** `acquire_predicted_property` only reranks and trims
  a similarity shortlist; its values are `experimental-acquisition-only` predictions. Never
  describe them as measured, safe, or as meeting an ADMET threshold. Only `where` and
  `require_preset` enforce a literal threshold, and only on exact assembled-product RDKit values.
- **Do not invent endpoints** such as batch search URLs or pricing APIs. The public v2
  operations are `search`, `search_cheese`, `search_substructure`, `sample`, `catalog`,
  `selections`, `runs`, and `optimizations`, all reached through this package. The
  optimization endpoints are exactly: `POST /api/v2/optimizations`, `GET /api/v2/optimizations`,
  `GET /api/v2/optimizations/{id}`, `GET /api/v2/optimizations/{id}/batch?wait=0..25`,
  `GET /api/v2/optimizations/{id}/batches/{batch_id}`,
  `POST /api/v2/optimizations/{id}/batches/{batch_id}:submit`,
  `GET /api/v2/optimizations/{id}/results`, `POST /api/v2/optimizations/{id}:cancel` and
  `POST /api/v2/optimizations/{id}:resume`.
- **Retries.** `DeepMedChemError` exposes `code`, `status_code`, `request_id`, and
  `retryable`. HTTP 429/503/504 are retried automatically twice; report `request_id` when
  escalating an error.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `missing_api_key` | `dmc login`, or export `DEEPMEDCHEM_API_KEY`; check `dmc status`. |
| HTTP 401 / 403 | Key revoked or wrong profile; `dmc login --profile default` again. |
| HTTP 429 or `remaining: 0` | Daily credits exhausted; `dmc usage` shows the reset time. |
| Substructure timeout | Increase `--timeout-seconds`, simplify the SMARTS, or lower `-n`. |
| Unknown database | Copy the exact `database_id` from `dmc databases --json`. |
| SDF export fails | `pip install "deepmedchem[sdf]"` (needs RDKit). |
| `optimization_not_enabled` (403) | Optimization is in an allowlisted beta; ask DeepMedChem for access. |
| `idempotency_conflict` on create | The name is taken by a different specification; choose a new `name`. |
| `optimization_paused` | Top up credits, then `dmc optimize resume NAME` and run again. |
| `invalid_scores` (422) | One row per proposed id; `error.details` lists missing/unknown/duplicate/invalid ids. |
| No keyring on a server | `DEEPMEDCHEM_CREDENTIAL_STORE=file` or use the env var. |

## Links

- Package: https://pypi.org/project/deepmedchem/ and https://github.com/Deep-MedChem/deepmedchem-python
- Python guides: https://docs.deepmedchem.com/docs/guides/python/quickstart and
  https://docs.deepmedchem.com/docs/guides/python/reference
- API concepts and OpenAPI: https://docs.deepmedchem.com/docs/guides/api/concepts and
  https://docs.deepmedchem.com/openapi-v2.json
- Curated docs index for agents: https://docs.deepmedchem.com/llms.txt
