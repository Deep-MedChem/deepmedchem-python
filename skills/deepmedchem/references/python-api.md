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

### Similarity thresholds

`maximize_similarity` sets the single ranking metric; `require_similarity` adds exact filters.
All thresholds must pass, a threshold may use the ranking metric, and call order does not matter.

```python
sel = (
    Selection.from_database("enamine-real-v5a")
    .reference("query", smiles="CC(=O)Oc1ccccc1C(=O)O")
    .require_similarity("rdkit.ecfp4_tanimoto", reference="query", gte=0.4, lt=0.85)  # interval
    .require_similarity("cheese.shape", reference="query", gte=0.7)
    .maximize_similarity("cheese.shape", reference="query")
    .limit(50)
)
```

- One call takes at most one lower (`gt`/`gte`) and one upper (`lt`/`lte`) bound.
- Metrics and ranges: `rdkit.ecfp4_tanimoto` 0 to 1; `cheese.shape` and `cheese.electrostatic`
  (embedding cosine) -1 to 1.
- Thresholds must use the objective's reference in a ranked selection with one objective;
  invalid thresholds are rejected with HTTP 422 before execution.
- Filtering applies to the candidate pool evaluated for this query, before `limit`; results can
  be fewer than `limit` (`partial_results` warning) and are not a global optimum over the space.

### Exact RDKit constraints and predicted-property acquisition

`where` and `require_preset` are literal constraints: the API recalculates every RDKit descriptor
on the assembled product and enforces the threshold exactly, so a returned molecule always
satisfies the constraint.

`acquire_predicted_property` adds one experimental acquisition stage that reranks and trims a
similarity shortlist. It requires a ranked strategy and a similarity objective, and the database
must advertise the endpoint.

```python
sel = (
    Selection.from_database("enamine-real-v5a")
    .reference("query", smiles="CCOc1ccc(C(=O)N2CCN(C)CC2)cc1")
    .maximize_similarity("rdkit.ecfp4_tanimoto", reference="query")
    .require_preset("lipinski-ro5/v1")
    .where("rdkit.mol_wt", lte=450, units="Da")
    .acquire_predicted_property(
        "openadmet-herg-pchembl", direction="minimize", keep_fraction=0.25
    )
    .include("properties", "objective_components")
    .limit(100)
)
result = client.selections.create(sel)
result.acquisition            # endpoint_id, model_version, direction, units, qualification,
                              # candidates_before, candidates_after
result.hits[0].properties     # exact assembled-product RDKit values
result.hits[0].acquisition    # predicted_value, applicable
```

CP16 values are `predicted`, `experimental-acquisition-only` ranking signals. They are not assay
results and never establish that an ADMET threshold is met; only `where`/`require_preset` do that.
A database that lacks the loaded property or CP16 assets answers `capability_unavailable` before
any credit is reserved, so read the catalog (`client.catalog()`) for each library's `properties`,
`presets`, `predicted_property_endpoints`, and
`capabilities.predicted_property_acquisition`. These controls exist only on `Selection`; the simple
`search`/`sample` helpers, the CLI, and the export helpers keep their ordinary interfaces.

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

## Optimizations (Navigator ask/tell)

```python
import deepmedchem as dmc

dmc.optimize(score, *, direction, database="enamine", budget=1000, batch_size=100, name=None,
             strategy=None, filters=None, properties=None, seed=None, scorer=None,
             metadata=None, progress=True, client=None) -> OptimizationResult
dmc.normalize_scores(batch, scores) -> list[dict]     # the submission rows tell()/optimize() send
```

- `score(list[str]) -> sequence` of the same length; each value is a number (valid), `None`/NaN
  (failed), or `{"score": x, "status"?, "error"?, **metrics}`. NumPy scalars work; bool and
  strings are rejected. A mapping `{id: value}` is also accepted by `tell`/`normalize_scores`;
  missing ids become `failed` with `error="not returned by scorer"`.
- `direction`: `"minimize"` or `"maximize"`, required. `filters`: `None` or `"druglike"`.
  `properties`: `{"MolWt": (None, 500)}` or `{"MolWt": {"min": 200, "max": 500}}`.
  `database` accepts aliases (`enamine`, `freedom`, `cheminfinita`).
- `name` is the idempotency/resume key; without it the SDK sends a random `Idempotency-Key` and
  prints the id. Progress goes to stderr. Unsubmitted scores are journaled under
  `platformdirs.user_cache_dir("deepmedchem")/optimizations/<id>/<batch>.json`
  (`DEEPMEDCHEM_CACHE_DIR` overrides).
- On create, a `completed` optimization returns its results; `cancelled`/`failed` raises
  `DeepMedChemError(code="optimization_not_active")`.

`client.optimizations` (`AsyncClient.optimizations` has the same methods as coroutines):

| Method | Request |
| --- | --- |
| `create(*, database, direction, budget, batch_size, name=None, strategy=None, filters=None, properties=None, seed=None, scorer=None, metadata=None, objective_name=None, units=None, idempotency_key=None) -> Optimization` | `POST /api/v2/optimizations` |
| `get(id_or_name) -> Optimization` | `opt_…` → `GET …/{id}`, else `GET …?name=` |
| `list(*, status=None, name=None, limit=None) -> list[OptimizationResource]` | `GET /api/v2/optimizations` (follows cursors) |
| `retrieve(id) -> OptimizationResource` | `GET …/{id}` |
| `next_batch(id, *, wait=0) -> (OptimizationResource, Batch \| None)` | one `GET …/{id}/batch?wait=` (0–25 s) |
| `batch(id, batch_id) -> Batch` | `GET …/{id}/batches/{batch_id}`: any issued batch, also after submission |
| `submit(id, batch_id, rows, *, scorer=None) -> SubmitReceipt` | `POST …/{id}/batches/{batch_id}:submit` (rows already normalized) |
| `results_page(id, *, order="best", limit=100, cursor=None) -> (list[Observation], next_cursor)` | `GET …/{id}/results` |
| `cancel(id)`, `resume(id) -> OptimizationResource` | `POST …/{id}:cancel`, `POST …/{id}:resume` |

`Optimization` / `AsyncOptimization` handles: `id`, `name`, `status`, `resource`, `refresh()`,
`ask(*, timeout=None) -> Batch | None` (long-polls, honours `Retry-After`, `None` when finished,
raises `optimization_paused` or `client_timeout`), `tell(batch_or_id, scores, *, scorer=None) ->
SubmitReceipt`, `results(*, order="best", limit=None) -> OptimizationResult`, `cancel()`,
`resume()`.

Models: `Batch` (`id`, `round`, `molecules`, `.smiles`, `.ids`, `len`, iteration,
`to_records()`, `to_csv(path)`), `Molecule` (`id`, `smiles`), `Observation` (`id`, `smiles`,
`score`, `status`, `round`, `metrics`, `error`), `OptimizationResult` (a sequence of
observations; `top(n=10)` valid only, best first; `best`; `optimization`; `to_records()`,
`to_csv()`, `to_pandas()`), `OptimizationResource` (`status`, `status_reason`, `round`,
`pending_batch_id`, `progress`, `best`, `specification`, `terminal`, `direction`),
`SubmitReceipt` (`accepted`, `duplicate`, `batch_id`, `counts`, `optimization`).

Statuses: `initializing`, `proposing`, `awaiting_scores` (a batch is pending), `ingesting`,
`paused`, and terminal `completed` (`budget_exhausted`, `space_exhausted`, ...), `failed`,
`cancelled`. Error codes include `idempotency_conflict`, `capability_unavailable`,
`optimization_not_enabled`, `tenant_optimization_quota_exceeded`, `credit_limit_exceeded`,
`invalid_scores`, `submission_conflict`, `batch_not_pending`, `optimization_not_active`,
`scorer_changed`, `optimization_not_paused`.

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

- `DeepMedChemError(message, code, status_code, request_id, retryable, field, details)` is
  raised for API and credential failures; `DMCError` is an alias. `CredentialError` covers keyring problems.
- Credentials resolve from the `api_key` argument, `DEEPMEDCHEM_API_KEY`, a custom
  `CredentialProvider`, the profile's OS-keyring entry, then `credentials.json`.
- Config lives in `config.toml` under the platform user config directory
  (`~/.config/deepmedchem/` on Linux). Profiles hold `api_url`, `web_url`, `account_url`;
  `DEEPMEDCHEM_API_URL`, `DEEPMEDCHEM_ACCOUNT_URL`, and `DEEPMEDCHEM_PROFILE` override them.
- Every request sends `X-DMC-Client`, `X-DMC-Client-Version`, and `X-DMC-SDK-Version`; set
  `application` and `application_version` on the client when embedding the SDK in a product.
