---
name: deepmedchem
description: >-
  Search make-on-demand chemical spaces (Enamine REAL, Freedom Space, ChemInfinita, Synple,
  VAST and others, billions to trillions of molecules) through the DeepMedChem platform with
  the `deepmedchem` Python package and its `dmc` CLI. Use when a task involves finding
  purchasable analogs or similar molecules for a SMILES (ECFP4/Morgan, 3D shape, or
  electrostatic similarity), exact SMILES/SMARTS substructure search, random sampling of a
  chemical space, exporting hits to CSV/SDF/SMILES, checking CHEESE Credits, building
  molecule-selection/1 documents or durable runs, or preparing vendor quote and order
  requests. Also use when the user mentions DeepMedChem, CHEESE, `dmc login`,
  `import deepmedchem`, or `DEEPMEDCHEM_API_KEY`.
license: MIT
compatibility: Requires Python 3.9+, `pip install deepmedchem`, and network access to api.deepmedchem.com with a CHEESE API key.
metadata:
  author: Deep MedChem
  version: "1.0"
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
| Ask vendors for quotes or orders | `dmc order results.csv --get-quote` | `prepare_order(...)` |

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
- **Do not invent endpoints** such as batch search URLs or pricing APIs. The public v2
  operations are `search`, `search_cheese`, `search_substructure`, `sample`, `catalog`,
  `selections`, and `runs`, all reached through this package.
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
| No keyring on a server | `DEEPMEDCHEM_CREDENTIAL_STORE=file` or use the env var. |

## Links

- Package: https://pypi.org/project/deepmedchem/ and https://github.com/Deep-MedChem/deepmedchem-python
- Python guides: https://docs.deepmedchem.com/docs/guides/python/quickstart and
  https://docs.deepmedchem.com/docs/guides/python/reference
- API concepts and OpenAPI: https://docs.deepmedchem.com/docs/guides/api/concepts and
  https://docs.deepmedchem.com/openapi-v2.json
- Curated docs index for agents: https://docs.deepmedchem.com/llms.txt
