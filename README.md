# DeepMedChem Python SDK

[![PyPI](https://img.shields.io/pypi/v/deepmedchem?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/deepmedchem/)
[![Python](https://img.shields.io/pypi/pyversions/deepmedchem?style=flat-square&logo=python&logoColor=white)](https://pypi.org/project/deepmedchem/)
[![CI](https://img.shields.io/github/actions/workflow/status/Deep-MedChem/deepmedchem-python/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/Deep-MedChem/deepmedchem-python/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)
[![Production API](https://img.shields.io/badge/API-api.deepmedchem.com-0A7EA4?style=flat-square)](https://api.deepmedchem.com/api/v2/docs)
[![Documentation](https://img.shields.io/badge/docs-docs.deepmedchem.com-4B32C3?style=flat-square)](https://docs.deepmedchem.com/)

The official, chemistry-thin Python client for the DeepMedChem hosted chemical-space platform.
It contains no RDKit, models, databases, or proprietary search implementation.

> **Beta:** `deepmedchem` 0.2 is available for early use. APIs may still change before the
> stable release.

## Installation

```bash
pip install deepmedchem
```

Authenticate once, or set `DEEPMEDCHEM_API_KEY` in automation:

```bash
dmc login
dmc status
```

`dmc login` prints a short code and an approval URL. On a desktop it opens the URL in your
browser; on a headless server, container, or SSH session it only prints the URL, which you can open
on any device. Sign in or create a CHEESE account there, approve the connection, and the CLI finishes
on its own. The key goes to the OS keyring when one is available, otherwise to a `credentials.json`
file (mode 0600) next to the SDK config. Use `--no-browser` to force the print-only behaviour and
`--token-stdin` to paste an existing key from a pipe.

## Command line

The `dmc` command (also installed as `deepmedchem`) covers the everyday operations without
writing Python:

```bash
dmc databases                        # searchable databases, delivery time, order emails
dmc usage                            # account plan and CHEESE Credits remaining today
dmc search "CC(=O)Oc1ccccc1C(=O)O" -d enamine-real-v5a -m shape -n 10
dmc search "CC(=O)Oc1ccccc1C(=O)O" -d enamine-real-v5a -o aspirin.csv
dmc substructure "[N;R0][N;R0]C(=O)" -d enamine-real-v5a -n 50 -o hydrazides.sdf
dmc sample -d freedom-space-5 -n 100 --seed 7 -o sample.smi
dmc order aspirin.csv --get-quote
```

`databases` lists every searchable space with its typical delivery time and the vendor address
for orders and quotes:

```text
$ dmc databases
database                name                  availability  orders
----------------------  --------------------  ------------  ------------------------
cheminfinita-2026-02    ChemInfinita 2026-02  3-6 weeks     sales@otavachemicals.com
d2b-spacem1             D2B SpaceM1           3-6 weeks     hello@molecule.one
enamine-real-v5a        Enamine REAL v5a      3-6 weeks     info@enamine.net
freedom-space-5         Freedom Space 5       3-6 weeks     sales@chem-space.com
synple-explore-2025-10  Synple eXplore        3-6 weeks     sales@emolecules.com
synple-synple-2025-10   Synple                3-6 weeks     sales@emolecules.com
vast-2026-h2            VAST 2026 H2          3-6 weeks     contact@xtalpi.com
```

Searches print a table of rank, similarity score, price, product id, and SMILES:

```text
$ dmc search "CC(=O)Oc1ccccc1C(=O)O" -d enamine-real-v5a -n 3
rank   score  price  product_id                smiles
----  ------  -----  ------------------------  ----------------------
   1  0.7037   $245  46abadcde3d6af9edc2a454e  O=C(O)Oc1ccccc1C(=O)O
   2  0.6667   $163  ed4fbbbb70795dd28f1a6189  COC(=O)Oc1ccccc1C(=O)O
   3  0.5312   $245  43d73d7ec9d5cbae8425cebe  O=C(O)COc1ccccc1C(=O)O

3 molecules, method=morgan, database=enamine-real-v5a, release=2026-09-02.1, metric='ECFP4 Tanimoto', 380 ms
```

`-o/--output` saves the hits as CSV, SDF, SMILES (`.smi`), or JSON, inferred from the file suffix
(`--format` overrides it). CSV and SDF carry the score, price, product id, and every other field
from the response. SDF output needs RDKit (`pip install "deepmedchem[sdf]"`); the other formats
have no extra dependencies. Every command accepts `--json` for the raw API response and
`--profile` to pick a configured profile.

## Requesting quotes and orders

Prepare vendor-ready requests directly from an exported result CSV:

```bash
dmc order results.csv --get-quote          # confirm prices and availability
dmc order results.csv --amount-mg 1        # initiate a 1 mg order request
dmc order results.csv --no-open             # files only; useful over SSH
```

The command groups molecules by vendor email, creates one directory per recipient, and then asks
the operating system to open a pre-filled email draft. It never sends email or places an order.
Every request remains available as `email.txt` plus `molecules.csv` if no graphical mail client is
available or a draft fails to open. DeepMedChem is CCed so vendors can attribute the request.

Vendor-facing molecule files contain only the database ID, a `-DMCH` reference ID, and SMILES.
Search scores, properties, and non-binding SDK price estimates are deliberately omitted. The
message asks the vendor to confirm final pricing, availability, lead time, and order details before
processing. Use `--to ADDRESS` for a private database without a configured procurement contact,
and `--database ID` for older CSV files that do not carry a database column.

```text
$ dmc usage
plan:      premium
credits:   9,999 of 10,000 remaining today (1 used)
resets:    2026-09-04T00:00:00+00:00 (in 13h 35m)
```

## Quickstart

```python
import deepmedchem as dmc

result = dmc.search(
    "CC(=O)OC1=CC=CC=C1C(=O)O",  # Aspirin
    database="enamine-real-v5a",
    method="shape",
    limit=3,
)

print(repr(result))
for hit in result.hits:
    price = f"${hit.price}" if hit.price is not None else "unavailable"
    print(f"{hit.rank}  score={hit.score:.4f}  price={price}  {hit.smiles}")
```

Example output (the database release and search results can change):

```text
SearchResult(3 molecules, method='shape', database='enamine-real-v5a')
1  score=0.9726  price=$245  O=C(O)Oc1ccccc1C(=O)O
2  score=0.9719  price=$163  COC(=O)Oc1ccccc1C(=O)O
3  score=0.8713  price=$245  O=C(O)COc1ccccc1C(=O)O
```

Prices are estimates in whole US dollars for delivery to the United States. They are returned in
the original search response, so both `hit.price` and the aligned `result.prices` list are
available without another API request. Databases without price estimates return `None`; run
`dmc databases` for the current list and the vendor address to request a binding quote.

## SMILES and SMARTS substructure search

Use `format="smiles"` for a concrete molecular graph, including the existing
junction-spanning examples. Use `format="smarts"` for atom lists, ring constraints,
recursive expressions, and other SMARTS query features:

```python
junction = dmc.substructure("CNC(=O)N1CCC1", format="smiles", database="enamine-real-v5a", limit=10)
hydrazides = dmc.substructure(
    "[N;R0][N;R0]C(=O)", format="smarts", database="enamine-real-v5a", limit=10
)
```

See the runnable [substructure example](examples/docs/substructure_search.py) for several
SQC-derived SMARTS queries. Complex recursive SMARTS can require a longer timeout.

Any result writes itself with `result.to_csv(path)`, `result.to_sdf(path)`, or
`result.to_file(path)` (format inferred from the suffix). `dmc.usage()` and `Client.usage()` return
the account plan and the daily CHEESE Credit balance (`plan`, `limit`, `used`, `remaining`,
`reset_at`, and an optional `promo`); the balance is served by the account service configured as
the profile's `account_url`.

Module-level `search`, `substructure`, `sample`, `catalog`, and `usage` operations create and close
a small internal client. The explicit `Client` remains available for connection reuse and advanced
selections/runs. Search results behave as ordered SMILES sequences (`result[0]`, `result[:3]`,
`list(result)`) while retaining typed hits, scores, prices, metadata, warnings, and the complete raw
response locally.

The default profile calls `https://api.deepmedchem.com`. Keys created at
`https://cheese.deepmedchem.com` work on both the legacy and v2 APIs. All keys for an account share
one daily CHEESE Credit balance: one successful synchronous execution or durable-run item costs one
credit. Synchronous work is terminated after 10 seconds; use the Runs API for longer work, where a
basic item has a 60-second limit.

Credentials resolve from an explicit `api_key`, `DEEPMEDCHEM_API_KEY`, compatibility environment
variables, a custom credential provider, the selected profile's OS-keyring entry, or the
`credentials.json` fallback file. Set `DEEPMEDCHEM_CREDENTIAL_STORE=file` or `=keyring` to force one
store. Use `dmc login --profile dev` for the development service; profiles never share
credentials.

Every request identifies its source with `X-DMC-Client`, `X-DMC-Client-Version`, and
`X-DMC-SDK-Version`. The default values attribute direct SDK use to `deepmedchem-python`; an
application such as Navigator can override `application` and `application_version` while retaining
the installed SDK version separately.

## Selections and durable runs

`Selection` and `Run` are immutable, chemistry-thin builders. They produce the public
`molecule-selection/1` and `run/1` documents; all chemistry and capability validation remains on
the API.

```python
from deepmedchem import Client, Run, Selection

template = (
    Selection.from_database("enamine-real-v5a")
    .ranked()
    .maximize_similarity("rdkit.ecfp4_tanimoto", reference="query")
    .limit(10)
)

run_spec = Run.selection_batch(
    template=template,
    items={
        "lead-001": {"query": "CCO"},
        "lead-002": {"query": "CCN"},
    },
)

with Client() as dmc:
    run = dmc.runs.create(run_spec, idempotency_key="lead-set-v1")
    terminal = dmc.runs.wait(run.id)
    results = list(dmc.runs.iter_results(terminal.id))
```

`AsyncClient` offers matching asynchronous operations and iterators. `DMCClient` and
`AsyncDMCClient` are compatibility aliases for code written against the pre-split Navigator SDK.

## Navigator

The `navigator` terminal application is distributed separately as `dmc-navigator`. It depends on
this SDK and adds file handling, login commands, terminal presentation, and Navigator-specific
workflows.

## Use with AI coding agents

The repository ships an [Agent Skill](https://agentskills.io) in
[`skills/deepmedchem`](skills/deepmedchem/SKILL.md) that teaches Claude Code, Codex, Cursor, and
other skill-aware agents how to search chemical space with this package and the `dmc` command.

```bash
# Claude Code: add the marketplace once, then install the plugin
/plugin marketplace add Deep-MedChem/deepmedchem-python
/plugin install deepmedchem@deep-medchem

# Any agent that supports the open skills format
npx skills add Deep-MedChem/deepmedchem-python
```

You can also copy `skills/deepmedchem/` into `.claude/skills/` of a project (or `~/.claude/skills/`
for every project), or zip the folder and upload it as a custom skill in Claude.ai or through the
Skills API.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[test]"
ruff check .
pytest
python -m build
twine check dist/*
```

API documentation: <https://docs.deepmedchem.com/docs/python/quickstart>

Runnable authenticated examples using the established Enamine query panels are in
[`examples/live`](examples/live/README.md).

For interactive RDKit visualization of similarity and SMARTS substructure queries, open the
[`Enamine search notebook`](examples/notebooks/enamine_search.ipynb).
