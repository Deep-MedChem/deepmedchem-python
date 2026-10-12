# DeepMedChem Python SDK

[![PyPI](https://img.shields.io/pypi/v/deepmedchem?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/deepmedchem/)
[![Python](https://img.shields.io/pypi/pyversions/deepmedchem?style=flat-square&logo=python&logoColor=white)](https://pypi.org/project/deepmedchem/)
[![CI](https://img.shields.io/github/actions/workflow/status/Deep-MedChem/deepmedchem-python/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/Deep-MedChem/deepmedchem-python/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)
[![Production API](https://img.shields.io/badge/API-api.deepmedchem.com-0A7EA4?style=flat-square)](https://api.deepmedchem.com/api/v2/docs)
[![Documentation](https://img.shields.io/badge/docs-docs.deepmedchem.com-4B32C3?style=flat-square)](https://docs.deepmedchem.com/)

The official, chemistry-thin Python client for the DeepMedChem hosted chemical-space platform.
It contains no RDKit, models, databases, or proprietary search implementation.

> **Beta:** `deepmedchem` 0.3 is available for early use. APIs may still change before the
> stable release.

## Installation

```bash
pip install deepmedchem
```

## Authentication

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

## Choose your path

The package includes both a **command-line interface (CLI)** and a **Python SDK**. Pick based on your use case:

| Task | CLI | Python SDK |
|------|-----|-----------|
| Quick similarity search | ✅ `dmc search` | ✅ `search()` |
| Substructure query | ✅ `dmc substructure` | ✅ `search_substructure()` |
| Random sampling | ✅ `dmc sample` | ✅ `sample()` |
| Complex selection (constraints + objectives) | ❌ | ✅ `Selection` builder |
| Batch runs (templates + parameters) | ❌ | ✅ `Run` builder |
| Create orders | ✅ `dmc order` | ❌ |
| One-liners / shell scripts | ✅ | ❌ |
| Automation / CI-CD pipelines | ✅ | ✅ |

## Command Line Interface (CLI)

The `dmc` command (also installed as `deepmedchem`) covers the everyday operations without
writing Python:

### Common commands

```bash
dmc databases                        # List all searchable spaces
dmc databases --detailed             # Full details, availability, links
dmc usage                            # Account plan and CHEESE Credits remaining
dmc search "CC(=O)Oc1ccccc1C(=O)O" -d enamine -m shape -n 10
dmc search "CC(=O)Oc1ccccc1C(=O)O" -d enamine -o aspirin.csv
dmc substructure "[N;R0][N;R0]C(=O)" -d enamine -n 10 --timeout-seconds 45 -o hydrazides.sdf
dmc sample -d freedom -n 100 --seed 7 -o sample.smi
dmc order aspirin.csv --get-quote
```

### Viewing available databases

`dmc databases` lists every searchable space with its size, whether per-compound price estimates
are available, and the vendor address for orders and quotes. For Enamine, the size is an estimated number of source reagent
combinations, marked with `~`, rather than unique molecular graphs. `--detailed` adds full IDs, BioSolveIT mappings, type, availability,
success estimates, and vendor links. `--json` always returns the unmodified API catalog:

```text
$ dmc databases
abbreviation  molecules  prices  orders
------------  ---------  ------  ------------------------
enamine          ~93.4B  yes     info@enamine.net
freedom          296.4B  yes     sales@chem-space.com
explore            9.5T  yes     sales@emolecules.com
synple             7.6T  yes     sales@emolecules.com
vast               6.8B  yes     VAST@XtalPi.com
cheminfinita     794.2B  -       sales@otavachemicals.com
spacem1            1.5B  -       hello@molecule.one

7 databases. Lead times vary by database; see --detailed.
Order or request quotes by email, or run `dmc order results.csv`.
```

### Similarity search

Searches print a table of rank, similarity score, price, and SMILES. The score range and execution time are shown after results.

```text
$ dmc search "CC(=O)Oc1ccccc1C(=O)O" -d enamine -n 3
rank   score  price  smiles
----  ------  -----  -----------------------------
   1  0.7037   $245  O=C(O)Oc1ccccc1C(=O)O
   2  0.6667   $163  COC(=O)Oc1ccccc1C(=O)O
   3  0.6061   $163  CC(C)(C)OC(=O)Oc1ccccc1C(=O)O

Searched ~93.4B source combinations (Enamine REAL v5a) in 396 ms.
Similarity range: 0.61-0.70 ECFP4 Tanimoto.
```

**Output formats:** `-o/--output` saves results as CSV, SDF, SMILES (`.smi`), or JSON (format inferred from suffix; use `--format` to override). 
CSV and SDF carry the score, price, product id, and all other fields from the response. 
SDF output requires RDKit (`pip install "deepmedchem[sdf]"`); other formats have no extra dependencies.

**Other options:**
- `--json` for raw API response
- `--profile` to use a specific credential profile

### Substructure search

Use `format="smiles"` for a concrete molecular graph or `format="smarts"` for SMARTS queries:

```bash
dmc substructure "CNC(=O)N1CCC1" -d enamine -n 10  # SMILES (default)
dmc substructure "[N;R0][N;R0]C(=O)" -d enamine -n 10 --timeout-seconds 45  # SMARTS
```

Substructure hits show `exact` instead of a similarity score.

### Sampling

Draw random molecules from a database:

```bash
dmc sample -d freedom -n 100 --seed 7 -o sample.smi
```

### Ordering

Prepare vendor-ready requests directly from a CSV result file:

```bash
dmc order results.csv --get-quote          # confirm prices and availability
dmc order results.csv --amount-mg 1        # initiate a 1 mg order request
```

The command groups molecules by vendor, creates one directory per recipient, and opens a pre-filled email draft. 
Every request remains as `email.txt` + `molecules.csv` if no graphical mail client is available.

---

## Python SDK

Use the Python SDK for programmatic access, complex workflows, and reproducible automation.

### Quick start

```python
import deepmedchem as dmc

result = dmc.search(
    "CC(=O)OC1=CC=CC=C1C(=O)O",  # Aspirin
    database="enamine",
    method="shape",
    limit=3,
)

print(repr(result))
for hit in result.hits:
    price = f"${hit.price}" if hit.price is not None else "unavailable"
    print(f"{hit.rank}  score={hit.score:.4f}  price={price}  {hit.smiles}")
```

Example output:

```text
SearchResult(3 molecules, method='shape', database='enamine-real-v5a')
1  score=0.9726  price=$245  O=C(O)Oc1ccccc1C(=O)O
2  score=0.9719  price=$163  COC(=O)Oc1ccccc1C(=O)O
3  score=0.9551  price=$163  COC(=O)Oc1ccccc1(C(C)=O
```

### Search methods

```python
from deepmedchem import Client

with Client() as client:
    # Similarity search (ECFP4 Tanimoto, morgan)
    hits = client.search("CC(=O)Oc1ccccc1C(=O)O", database="enamine", limit=10)
    
    # CHEESE search (shape or electrostatic similarity)
    hits = client.search_cheese(
        "CC(=O)Oc1ccccc1C(=O)O",
        database="enamine",
        scorer="shape",
        limit=20,
    )
    
    # Substructure search
    hydrazides = client.search_substructure(
        "[N;R0][N;R0]C(=O)",
        query_format="smarts",
        database="enamine",
        limit=20,
        timeout_seconds=45,
    )
    
    # Random sampling
    sample = client.sample(database="freedom", count=100, seed=7)

# Export results
hits.to_csv("results.csv")
hits.to_sdf("results.sdf")  # Requires RDKit
hits.to_file("results.json")
```

### Selections: Complex queries with constraints and objectives

`Selection` is a fluent builder for sophisticated molecule discovery workflows. Define constraints,
objectives, and acquisition strategies for reproducible, auditable selections:

```python
from deepmedchem import Client, Selection

selection = (
    Selection.from_database("enamine-real-v5a")
    .reference(
        "query",
        smiles="CCOc1ccc(C(=O)N2CCN(C)CC2)cc1",
    )
    .ranked()  # Rank by objective
    .maximize_similarity("rdkit.ecfp4_tanimoto", reference="query")
    .require_preset("lipinski-ro5/v1")  # Add constraint
    .where("rdkit.mol_wt", lte=450, units="Da")  # Property constraint
    .acquire_predicted_property(
        "openadmet-herg-pchembl",
        direction="minimize",
        keep_fraction=0.25,
    )
    .include("properties", "objective_components")
    .limit(100)
)

# Validate before running
with Client() as client:
    validation = client.selections.validate(selection)
    
    # Estimate cost/effort
    estimate = client.selections.estimate(selection)
    
    # Execute
    result = client.selections.create(selection)
    print(f"Found {len(result)} molecules")
    print(result.smiles)
```

**Selection methods:**
- `from_database(db)` — Initialize from a database
- `reference(id, smiles=...)` or `.reference(id, smarts=...)` — Add reference molecules
- `ranked()` or `.sample()` — Choose strategy
- `maximize_similarity(metric, reference=...)` — Add objective
- `require_preset(preset_id)` — Apply property presets
- `where(property, gt/gte/lt/lte/range=..., units=...)` — Property constraints
- `where_predicted_property(endpoint, ...)` — Predicted property constraints
- `require_pattern(pattern_id)` — Structure pattern constraints
- `require_different_scaffold(method, reference=...)` — Scaffold constraints
- `acquire_predicted_property(endpoint, direction, keep_fraction)` — Predict and filter
- `limit(n)` — Result limit (1–1000)
- `max_per_scaffold(n)` — Diversity control
- `include(field, ...)` — Include extra response fields

### Batch runs: Templates and parameters

Run a selection template with different parameter sets:

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
        "lead-003": {"query": "CCCO"},
    },
)

with Client() as client:
    run = client.runs.create(run_spec, idempotency_key="batch-v1")
    
    # Wait for completion
    terminal = client.runs.wait(run.id)
    
    # Iterate results
    for item in client.runs.iter_results(run.id):
        print(f"{item.id}: {item.status}")
```

### Features available in the API

All databases searchable from Python:

- **Similarity search**: ECFP4 Tanimoto, CHEESE shape, CHEESE electrostatic
- **Substructure search**: Exact assembled-product SMILES/SMARTS matching
- **Sampling**: Random molecule draws with seed control
- **Selections**: Complex queries with constraints, objectives, property acquisition
- **Runs**: Batch templates with parameter substitution
- **Catalog**: List all available databases, properties, presets, and endpoints

**Limited features** on enumerated/in-stock catalogs (served by CHEESE Search, not the platform API):
- Similarity search only (no substructure, sampling, selections, or runs)
- 1–100 hits per call
- No per-compound price estimates

## DeepMedChem All Chemical Spaces

Snapshot: September 11, 2026. Use the abbreviation in `database="enamine"` or `dmc search ... -d enamine`.
Full database IDs remain supported. Abbreviations resolve to the releases listed by `dmc databases --detailed`.

| Abbreviation | Type[^python-availability] | Size | Availability | Success rate | Prices[^price-estimates] | Orders | Link |
| --- | --- | ---: | --- | --- | :---: | --- | :---: |
| `enamine` | Make-On-Demand | ~93.41B source combinations | 3–4 weeks | >80% | yes | info@enamine.net | [🔗](https://enamine.net/compound-collections/real-compounds/real-space-navigator) |
| `freedom` | Make-On-Demand | 296.4B | 5–6 weeks | >80% | yes | sales@chem-space.com | [🔗](https://chem-space.com/freedom-space) |
| `explore` | Make-On-Demand | 9.5T | 3–4 weeks | >85% | yes | sales@emolecules.com | [🔗](https://www.emolecules.com/products/explore) |
| `synple` | Make-On-Demand | 7.6T | 3–4 weeks | >85% | yes | sales@emolecules.com | [🔗](https://www.emolecules.com/products/explore) |
| `cheminfinita` | Make-On-Demand | 794.2B | 5–8 weeks | 55–85% | - | sales@otavachemicals.com | [🔗](https://www.otavachemicals.com/) |
| `spacem1` | Make-On-Demand | 1.5B | 2–6 weeks | >85% | - | hello@molecule.one | [🔗](https://molecule.one/) |
| `vast` | Make-On-Demand | 6.8B | 2–4 weeks | 85%+ | yes | VAST@XtalPi.com | [🔗](https://aifchem.com/) |
| `enamine-real` | Make-On-Demand (enumerated)[^cheese-search] | 9.56B | 3–4 weeks | >80% | - | libraries@enamine.net | [🔗](https://enamine.net/compound-collections/real-compounds) |
| `chemspace-5b-ro5` | Make-On-Demand (enumerated)[^cheese-search] | 5.0B | 5–6 weeks | >80% | - | sales@chem-space.com | [🔗](https://chem-space.com/) |
| `chemspace-5b-beyond-ro5` | Make-On-Demand (enumerated)[^cheese-search] | 5.0B | 5–6 weeks | >80% | - | sales@chem-space.com | [🔗](https://chem-space.com/) |
| `chemspace-5b-freedom` | Make-On-Demand (enumerated)[^cheese-search] | 5.0B | 5–6 weeks | >80% | - | sales@chem-space.com | [🔗](https://chem-space.com/freedom-space) |
| `xtalpi` | Make-On-Demand (enumerated)[^cheese-search] | 4.7B | 2–4 weeks | 85%+ | - | VAST@XtalPi.com | [🔗](https://aifchem.com/) |
| `synple-4b` | Make-On-Demand (enumerated)[^cheese-search] | 3.9B | 3–4 weeks | >85% | - | sales@emolecules.com | [🔗](https://www.emolecules.com/products/explore) |
| `chemriya` | Make-On-Demand (enumerated)[^cheese-search] | 1.4B | Ask vendor | N/A | - | info@chemriya.com | [🔗](https://www.otavachemicals.com/) |
| `molecule-one` | Make-On-Demand (enumerated)[^cheese-search] | 68.0M | 2–6 weeks | >85% | - | hello@molecule.one | [🔗](https://molecule.one/) |
| `explore-enumerated` | Make-On-Demand (enumerated)[^cheese-search] | 54.8M | 3–4 weeks | >85% | - | sales@emolecules.com | [🔗](https://www.emolecules.com/products/explore) |
| `explore-diverse` | Make-On-Demand (enumerated)[^cheese-search] | 11.4M | 3–4 weeks | >85% | - | sales@emolecules.com | [🔗](https://www.emolecules.com/products/explore) |
| `enamine-aa` | Make-On-Demand (enumerated)[^cheese-search] | 373.8K | 3–4 weeks | >80% | - | info@enamine.net | [🔗](https://enamine.net/) |
| `mcule-in-stock` | In-Stock[^cheese-search] | 7.2M | Immediate | 100% | yes | order@mcule.com | [🔗](https://mcule.com/) |
| `mcule-full`[^cheese-search] | In-Stock | 140.4M | Immediate | 100% | yes | order@mcule.com | [🔗](https://mcule.com/) |
| `molport`[^cheese-search] | In-Stock | 5.9M | Immediate | 100% | yes | sales@molport.com | [🔗](https://molport.com/) |
| `chemspace-screening`[^cheese-search] | In-Stock | 7.5M | Immediate | 100% | yes | sales@chem-space.com | [🔗](https://chem-space.com/) |
| `zinc15`[^cheese-search] | Other | 697.1M | No availability info | N/A | unknown | N/A | [🔗](https://zinc15.docking.org/) |

[^python-availability]: All rows are searchable from this package. Make-On-Demand spaces use the platform API with its full feature set. Rows marked [^cheese-search] are served by CHEESE Search; see *Enumerated and in-stock catalogues* below for what the SDK supports there.

[^cheese-search]: Served by CHEESE Search (`api.cheese.deepmedchem.com`) rather than the platform API: similarity search only (`morgan`, `shape`, `esp`), at most 100 hits per call, no per-compound price estimates, no substructure search, sampling, selections or runs. Sizes from the live catalogue on September 24, 2026.

[^price-estimates]: Prices are planning estimates per compound, before any discounts are applied, and generally assume a low number of molecules ordered and price per 1 mg. For instance VAST H2 2026 uses 1 mg per compound in a 50-compound order. Other databases similarly assume small orders; the exact basis varies by vendor. Request a final quote for your actual quantity and order size. Counts and price support for Make-On-Demand spaces come from the live API catalog. In-Stock rows use immediate availability and 100% success as stock-catalog conventions; confirm fulfillment with the vendor. MCULE-FULL includes the full purchasable catalog.

## Migrating from BioSolveIT InfiniSee

Use the DeepMedChem abbreviation in Python or with `-d`. The BioSolveIT slugs below identify
analogous collections; they are not accepted as DeepMedChem database IDs. Releases and molecule
counts differ. [BioSolveIT download catalog](https://www.biosolveit.de/chemical-spaces/).

| DeepMedChem abbreviation | BioSolveIT slug |
| --- | --- |
| `enamine` | `REALSpace_95bn_2026-04`** |
| `freedom` | `FreedomSpace_296bn_2026-03` |
| `explore` | `eXplore_8tr_2026-06` |
| `synple` | `Synple_8tr_2026-06` |
| `cheminfinita` | `CHEMriya_55bn_2025-10`* |
| `vast` | `VAST_4bn_2026-05` |

\* No direct mapping: CHEMriya is a related Otava collection, not ChemInfinita. But it might be very similar. Contact Otava for details.

\*\* No direct mapping: our Enamine version is built from public Enamine building blocks, so it is not identical to the BioSolveIT version. But it is very similar.

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

Account-specific usage snapshot from September 6, 2026:

```text
$ dmc usage
plan:      premium
credits:   9,992 of 10,000 remaining today (8 used)
resets:    2026-09-07T00:00:00+00:00 (in 10h 59m)
promo:     10x September promo (10x, base 1,000/day, until 2026-10-01)
```

---

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

API documentation: <https://docs.deepmedchem.com/docs/guides/python/quickstart>

Runnable authenticated examples using the established Enamine query panels are in
[`examples/live`](examples/live/README.md).

For interactive RDKit visualization of similarity and SMARTS substructure queries, open the
[`Enamine search notebook`](examples/notebooks/enamine_search.ipynb).

## Links

- [DeepMedChem website](https://deepmedchem.com/)
- [CHEESE UI](https://cheese.deepmedchem.com/) and [database overview](https://cheese.deepmedchem.com/about)
- [API](https://api.deepmedchem.com/) and [API reference](https://api.deepmedchem.com/api/v2/docs)
- [Documentation](https://docs.deepmedchem.com/) and [Python quickstart](https://docs.deepmedchem.com/docs/guides/python/quickstart)
- [Python package on PyPI](https://pypi.org/project/deepmedchem/)
- [Python SDK on GitHub](https://github.com/Deep-MedChem/deepmedchem-python)
