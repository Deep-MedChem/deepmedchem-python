# DeepMedChem Python SDK

The official, chemistry-thin Python client for the DeepMedChem hosted chemical-space platform.
It contains no RDKit, models, databases, or proprietary search implementation.

> **Pre-release:** `deepmedchem` 0.1 is being prepared and is not yet published on PyPI.

## Installation

After the first public release:

```bash
pip install deepmedchem
```

OS-keyring helpers are optional:

```bash
pip install "deepmedchem[auth]"
```

## Quickstart

```python
from deepmedchem import Client

with Client(api_key="...") as dmc:
    catalog = dmc.catalog()
    hits = dmc.search("CCO", database="enamine-real-v5a", limit=20)
    shape_hits = dmc.search_cheese(
        "CCO",
        database="enamine-real-v5a",
        scorer="shape",
        limit=20,
    )
    motif_hits = dmc.search_substructure(
        "C(=O)N1CCC1",
        query_format="smarts",
        database="enamine-real-v5a",
    )
    molecules = dmc.sample(
        database="enamine-real-v5a",
        count=100,
        seed=12345,
    )
```

The client reads credentials from an explicit `api_key`, `DEEPMEDCHEM_API_KEY`, `DMC_API_KEY`, the
existing `CHEESE_API_KEY`, a custom credential provider, or the shared OS-keyring entry. Explicit
credentials take precedence.

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

with Client(api_key="...") as dmc:
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
