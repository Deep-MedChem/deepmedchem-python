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

dmc = Client(api_key="...")

result = dmc.search(
    "CC(=O)OC1=CC=CC=C1C(=O)O",  # Aspirin
    database="enamine-real-v5a",
    limit=3,
)

print(f"database={result.database_id} release={result.database_release}")
for hit in result.results:
    print(f"{hit['rank']}  score={hit['score']:.4f}  {hit['smiles']}")

dmc.close()
```

Example output (the database release and search results can change):

```text
database=enamine-real-v5a release=2026-08-29.1
1  score=0.9726  O=C(O)Oc1ccccc1C(=O)O
2  score=0.9719  COC(=O)Oc1ccccc1C(=O)O
3  score=0.8713  O=C(O)COc1ccccc1C(=O)O
```

The client reads credentials from an explicit `api_key`, `DEEPMEDCHEM_API_KEY`, `DMC_API_KEY`, the
existing `CHEESE_API_KEY`, a custom credential provider, or the shared OS-keyring entry. Explicit
credentials take precedence.

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

For an interactive RDKit visualization of the query and a labeled result grid, open the
[`Enamine search notebook`](examples/notebooks/enamine_search.ipynb).
