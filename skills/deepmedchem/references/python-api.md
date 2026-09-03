# `deepmedchem` Python API reference

Full documentation: https://docs.deepmedchem.com/docs/guides/python/reference

## Module-level operations

Each function creates a short-lived client, performs one request, and closes the transport.
All accept `api_key`, `api_url`, `profile`, `credential_provider`, and `timeout` (seconds,
default 45.0) as keyword arguments.

```python
import deepmedchem as dmc

dmc.search(smiles, *, database, method="morgan", limit=20, shortlist_multiplier=10,
           include_synthons=False) -> SearchResult
dmc.substructure(query, *, database, format="smarts", limit=100, timeout_seconds=30,
                 include_synthons=False) -> SubstructureResult
dmc.sample(*, database, count=100, seed=None, include_synthons=False) -> SampleResult
dmc.catalog() -> dict            # {"libraries": [{"database_id", "name", "product_count", "pricing", ...}], ...}
dmc.usage() -> Usage
```

- `method` must be `"morgan"`, `"shape"`, or `"esp"`; non-Morgan methods call the CHEESE
  3D endpoint.
- `shortlist_multiplier` widens the server-side candidate pool before re-ranking; leave the
  default unless recall is a problem.
- `include_synthons=True` adds building-block information to each hit when the database
  supports it.

## Clients

```python
from deepmedchem import Client, AsyncClient

Client(api_key=None, *, api_url=None, profile=None, timeout=45.0, transport=None,
       credential_provider=None, application="deepmedchem-python", application_version=None,
       max_retries=2, retry_backoff=0.25, account_url=None)
```

Methods on `Client` (all mirrored as coroutines on `AsyncClient`):

| Method | Notes |
| --- | --- |
| `catalog()` | Public catalog document. |
| `usage()` | `Usage` from the account service. |
| `search(smiles, *, database, method="morgan", limit=20, shortlist_multiplier=10, include_synthons=False)` | Dispatches to `search_cheese` for `shape`/`esp`. |
| `search_cheese(smiles, *, database, scorer, limit=20, shortlist_multiplier=10, include_synthons=False)` | `scorer` is `"shape"` or `"esp"`. |
| `search_substructure(query, *, query_format="smarts", database, limit=100, timeout_seconds=30, include_synthons=False)` | Exact matches. |
| `sample(*, database, count=100, seed=None, include_synthons=False)` | Random molecules. |
| `selections.validate(sel)`, `selections.estimate(sel)`, `selections.create(sel)` | Selection documents, see below. |
| `runs.estimate(run)`, `runs.create(run, *, idempotency_key)`, `runs.retrieve(id)`, `runs.events(id, after=0)`, `runs.watch(id, after=0, poll_interval=0.5)`, `runs.wait(id, timeout=None, poll_interval=0.5)`, `runs.iter_results(id, order="completion")`, `runs.cancel(id)` | Durable runs. |
| `close()` | Also via `with Client() as c:`. |

Use one `Client` for many calls. Requests are retried up to `max_retries` times on 429, 503,
and 504 with exponential backoff, honouring `Retry-After`.

`DMCClient` and `AsyncDMCClient` are compatibility aliases for older Navigator code.

## Result models

`SearchResult` (and its subclasses `SubstructureResult`, `SampleResult`, `SelectionResult`)
is both a typed model and an ordered sequence of SMILES strings.

```python
result = dmc.search("CCO", database="enamine-real-v5a", limit=5)

result[0]; result[:3]; list(result); len(result)     # SMILES sequence
result.hits          # tuple[Hit]: smiles, rank, score, product_id, reaction_id, metric, price, .extra
result.smiles; result.scores; result.ids; result.ranks; result.prices   # aligned lists
result.meta          # SearchMeta: request_id, database, release, method, metric, returned, elapsed_ms
result.warnings      # tuple[WarningMessage]: code, message
result.raw           # the complete JSON payload
result.to_records()  # list[dict] with every field per hit
result.to_csv(path); result.to_sdf(path); result.to_file(path, format=None)
result.to_pandas()   # DataFrame, requires pandas
```

`Hit.price` is an `int | None` estimate in whole US dollars. `SampleResult` adds
`sampling_method`, `sampling_version`, `seed`.

`Usage` fields: `plan`, `limit`, `used`, `remaining`, `unlimited`, `window`, `reset_at`,
`seconds_to_reset`, `promo` (`label`, `multiplier`, `base_limit`, `ends_at`), `user_id`.

## Export helpers

`deepmedchem.export` backs the `to_*` methods and the CLI `-o` flag:

```python
from deepmedchem.export import infer_format, write_result
write_result(result, "hits.sdf", format=None)   # csv | sdf | smi | json, inferred from suffix
```

SDF needs RDKit (`pip install "deepmedchem[sdf]"`). CSV columns are the union of all hit fields.

## Selections (`molecule-selection/1`)

`Selection` is an immutable builder producing the public selection document. Chemistry and
capability validation happen on the API, so always `validate` before `create`.

```python
from deepmedchem import Client, Selection

sel = (
    Selection.from_database("enamine-real-v5a", release=None)       # release pins a snapshot
    .reference("aspirin", smiles="CC(=O)Oc1ccccc1C(=O)O")           # or smarts=...
    .ranked()                                                        # or .sample(distribution=..., seed=...)
    .maximize_similarity("rdkit.ecfp4_tanimoto", reference="aspirin")
    .require_different_scaffold("rdkit.bemis_murcko", reference="aspirin")
    .require_pattern("alpha-amino-acid/v1", min_count=1)
    .require_preset("lipinski-ro5/v1")
    .where("rdkit.mol_wt", gt=250, units="Da", fidelity="exact_product", missing="reject")
    .limit(100)
    .shortlist_multiplier(10)
    .max_per_scaffold(5)
    .include("properties", "constraint_evidence", "objective_components", "execution_plan")
)
sel.to_dict(); sel.to_json(); sel.to_yaml(); Selection.model_validate(sel.to_dict())

with Client() as client:
    validation = client.selections.validate(sel)      # .valid, .normalized_selection, .selection_hash, .warnings
    estimate = client.selections.estimate(validation.normalized_selection)   # .execution_tier, .work
    if estimate.execution_tier == "synchronous":
        result = client.selections.create(sel)         # SelectionResult, a SearchResult with id/status
```

`where` takes exactly one of `gt`, `gte`, `lt`, `lte`, or `range=(lo, hi)`. Property ids,
presets, pattern ids, and metrics are validated server-side; the catalog lists what a release
supports. When `estimate.execution_tier` is not synchronous, submit the selection as a run.

## Durable runs (`run/1`)

Use runs for anything that would exceed the 10 second synchronous budget or for many queries at
once. Each item costs one credit on success.

```python
from deepmedchem import Client, Run, Selection

template = (
    Selection.from_database("enamine-real-v5a")
    .ranked()
    .maximize_similarity("rdkit.ecfp4_tanimoto", reference="query")
    .limit(10)
)
spec = Run.selection_batch(
    template=template,
    items={"lead-001": {"query": "CCO"}, "lead-002": {"query": "CCN"}},   # item id -> {reference id: SMILES}
    metadata={"project": "demo"},
)
# Run.selection(sel) wraps one selection that needs durable execution.

with Client() as client:
    client.runs.estimate(spec)
    run = client.runs.create(spec, idempotency_key="lead-set-v1")   # reuse the key to resume safely
    for event in client.runs.watch(run.id, after=run.last_event_sequence):
        print(event.type)
    terminal = client.runs.wait(run.id, timeout=600)
    for item in client.runs.iter_results(run.id):                     # RunItem
        print(item.id, item.status, item.result if item.ok else item.error)
```

`RunResource` fields: `id`, `kind`, `status`, `progress` (`total`, `pending`, `running`,
`succeeded`, `failed`, `cancelled`), `last_event_sequence`, `terminal`. `client.runs.cancel(id)`
stops a run.

## Ordering helpers

```python
from deepmedchem import prepare_order

bundle = prepare_order("hits.csv", get_quote=True, database=None, output_dir=None,
                       to=None, amount_mg=None, name=None)
print(bundle.directory, bundle.mode, bundle.molecule_count)
for draft in bundle.drafts:            # one OrderDraft per vendor
    print(draft.vendor, draft.email, draft.directory)   # directory holds email.txt and molecules.csv
    draft.subject, draft.body, draft.mailto_url, draft.csv_path, draft.email_path, draft.molecules
```

The CSV needs a `smiles` column and either a `database_id`/`database` column or the `database`
argument. Only database id, a `-DMCH` reference id, and SMILES reach the vendor files.

## Errors and configuration

- `DeepMedChemError(message, code, status_code, request_id, retryable)` is raised for API and
  credential failures; `DMCError` is an alias. `CredentialError` covers keyring problems.
- Credentials resolve from the `api_key` argument, `DEEPMEDCHEM_API_KEY`, a custom
  `CredentialProvider`, the profile's OS-keyring entry, then `credentials.json`.
- Config lives in `config.toml` under the platform user config directory
  (`~/.config/deepmedchem/` on Linux). Profiles hold `api_url`, `web_url`, `account_url`;
  `DEEPMEDCHEM_API_URL`, `DEEPMEDCHEM_ACCOUNT_URL`, and `DEEPMEDCHEM_PROFILE` override them.
- Every request sends `X-DMC-Client`, `X-DMC-Client-Version`, and `X-DMC-SDK-Version`; set
  `application` and `application_version` on the client when embedding the SDK in a product.
