# DeepMedChem Python SDK

The official, chemistry-thin Python client for the DeepMedChem hosted chemical-space platform.
It contains no RDKit, models, databases, or proprietary search implementation.

> **Beta:** `deepmedchem` 0.2 is available for early use. APIs may still change before the
> stable release.

## Installation

```bash
pip install deepmedchem
```

Authenticate once with the OS credential store, or set `DEEPMEDCHEM_API_KEY` in automation:

```bash
deepmedchem login
deepmedchem status
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

Prices are whole US dollars for delivery to the United States and default to 1 mg where the
vendor uses pack sizes. They are returned in the original search response, so both `hit.price`
and the aligned `result.prices` list are available without another API request. An unavailable
price is `None`.

| Database | Price available | Basis |
| --- | --- | --- |
| Freedom Space 5 | Yes | $250 at 1 mg |
| Enamine REAL | Yes | $163 or $245, selected by the trained factorized model |
| eMolecules Synple | Yes | Building-block prices plus reaction price |
| eMolecules eXplore | Yes | Building-block prices plus reaction price |
| XtalPi VAST 2026 H2 | Yes | $118 one-step or $206 two-step estimate at 1 mg |
| d2b / molecule.one | No | — |
| ChemInfinita | No | — |

## SMILES and SMARTS substructure search

Use `format="smiles"` for a concrete molecular graph, including the existing
junction-spanning examples. Use `format="smarts"` for atom lists, ring constraints,
recursive expressions, and other SMARTS query features:

```python
junction = dmc.substructure(
    "CNC(=O)N1CCC1", format="smiles", database="enamine-real-v5a", limit=10
)
hydrazides = dmc.substructure(
    "[N;R0][N;R0]C(=O)", format="smarts", database="enamine-real-v5a", limit=10
)
```

See the runnable [substructure example](examples/docs/substructure_search.py) for several
SQC-derived SMARTS queries. Complex recursive SMARTS can require a longer timeout.

Module-level `search`, `substructure`, `sample`, and `catalog` operations create and close a small
internal client. The explicit `Client` remains available for connection reuse and advanced
selections/runs. Search results behave as ordered SMILES sequences (`result[0]`, `result[:3]`,
`list(result)`) while retaining typed hits, scores, prices, metadata, warnings, and the complete raw
response locally.

The default profile calls `https://api.deepmedchem.com`. Keys created at
`https://cheese.deepmedchem.com` work on both the legacy and v2 APIs. All keys for an account share
one daily CHEESE Credit balance: one successful synchronous execution or durable-run item costs one
credit. Synchronous work is terminated after 10 seconds; use the Runs API for longer work, where a
basic item has a 60-second limit.

Credentials resolve from an explicit `api_key`, `DEEPMEDCHEM_API_KEY`, compatibility environment
variables, a custom credential provider, or the selected profile's OS-keyring entry. Use
`deepmedchem login --profile dev` for the development service; profiles never share credentials.

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
