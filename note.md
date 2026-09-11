# Working notes — DeepMedChem API investigation (2026-09-10)

## Context

While building `examples/prompts/004_low_tanimoto_high_esp_by_price.ipynb` (a scaffold-hopping
style query: molecules with low 2D/Tanimoto similarity but high 3D/ESP-cosine similarity to a
query, from Enamine REAL), we hit two separate backend issues. Investigated live against
`https://api.deepmedchem.com` with the `deepmedchem` 0.3.0b3 Python client.

## Issue 1 — durable Runs backend down (RESOLVED)

- **Symptom:** every durable run failed at execution (not creation) with
  `503 {'code': 'credits_unavailable', 'message': 'Credit accounting is unavailable.'}`.
  Reproduced on 6+ attempts over ~15 minutes, including a trivial single-objective selection.
- **Not an account/quota issue:** `dmc.usage()` showed plan `premium`, 999,997/1,000,000
  credits remaining throughout.
- **Root cause (per operator):** the scheduler has an account ID but no submitting key ID for
  account-backed background runs; the credit RPC received an empty string for the key ID,
  which violated the ledger's API-key foreign key (Postgres 23503). Fix: send SQL `NULL`
  instead of an empty string for the missing key, while keeping account-wide metering/
  attribution for ordinary requests.
- **Status:** fixed and confirmed live (2026-09-10, idempotency key
  `post-fix-verify-20260910-001` completed successfully).
- **Note:** runs created *before* the fix are stuck failed and need a **new idempotency key**
  to retry post-deployment — reusing an old key will replay the old failure.

## Issue 2 — only one similarity objective per Selection, platform-wide (OPEN)

Once Issue 1 was fixed, a *different* error surfaced when a `Selection` declared two
similarity objectives (e.g. `cheese.electrostatic` + `rdkit.ecfp4_tanimoto`):

```
this release executes one similarity objective and one reference
```

This was returned as a **hard execution failure** (not just a "needs durable tier" note from
`estimate()`, which is how we'd previously read the same sentence).

- Confirmed failing identically on 4 databases: `enamine-real-v5a`, `freedom-space-5`,
  `cheminfinita-2026-02`, `synple-synple-2025-10`. Looks like a platform-wide limit, not a
  per-release quirk.
- `selections.validate()` accepts a 2-objective payload as schema-valid (`valid: True`) —
  the rejection only happens at actual execution. Validation does not catch this.

### Why this matters

Combined with two other confirmed facts:
- A plain `dmc.search()` hit carries no secondary similarity score (checked raw JSON of an
  ESP hit — only the ESP `score` and an unrelated `proposal_score`, no hidden Tanimoto field).
- There is no "compute similarity of these two specific molecules" endpoint anywhere in the
  public API (`catalog`, `usage`, `search`, `search_cheese`, `search_substructure`, `sample`,
  `selections.*`, `runs.*` — nothing fits).

**There is currently no way, using only the public `deepmedchem` package, to attach a second
similarity metric (e.g. Tanimoto) to molecules that were retrieved by a different similarity
metric (e.g. ESP cosine).** This blocks prompt 004 as literally specified, and any prompt with
the same shape ("retrieve by metric A, report/sort by metric B too").

### Possible asks for the operator

- Lift the one-similarity-objective limit (even just for durable runs), or
- Add a lightweight "score these candidate SMILES/product_ids against a reference by metric
  X" endpoint that doesn't require a full retrieval, or
- Have `selections.validate()` reject multi-objective selections at validation time instead
  of only failing at execution, so this is caught before a durable run is spent on it.

## Other confirmed API facts from this investigation (candidates for `guidelines.md`)

- Similarity objectives are maximize-only; server rejects a hand-constructed
  `"direction": "minimize"` with `Input should be 'maximize'`.
- Valid similarity `metric_id`s: `rdkit.ecfp4_tanimoto`, `cheese.shape`, `cheese.electrostatic`.
- No `weight` field on an objective (`Extra inputs are not permitted`).
- Synchronous `dmc.search()` hard-caps `limit` at 200 (`limit=201` rejected: "Input should be
  less than or equal to 200"); no offset/cursor param exists to page further.
- `Selection.limit()` is capped at 1000 by the SDK itself, regardless of sync/durable.
- Intersecting two independent "top-K most similar by metric" searches to find molecules that
  are *dissimilar* by one of those metrics cannot work: a dissimilar molecule never appears in
  a "most similar" top-K by definition. (We made this mistake in an earlier draft of
  example 004 — do not repeat it.)

## Issue 3 — `enamine-real-v5a` does not support substructure search

Confirmed live via `dmc.catalog()`: `enamine-real-v5a`'s capability block reports
`search_substructure: False`. `dmc.substructure(...)` / `client.search_substructure(...)`
against this database will not work.

- Databases confirmed **to** support substructure search (`search_substructure: True` in the
  catalog): `freedom-space-5`, `cheminfinita-2026-02`, `synple-explore-2025-10`,
  `synple-synple-2025-10`, `vast-2026-h2`.
- This is a per-database catalog capability, not a package limitation — check
  `dmc.catalog()["libraries"]` (or `dmc databases --json`) for a given database's
  `capabilities.search_substructure` before writing any example that needs it, rather than
  assuming every combinatorial/enumerated database supports the same operations.
- Hit while building example `005_logp_optimization.ipynb`: the prompt named Enamine REAL
  specifically, but the "retain this fixed fragment, vary the rest" approach needs
  substructure search, which Enamine REAL doesn't have. Example `003` hit the same
  requirement earlier and used `freedom-space-5` instead, which does support it.
- Added to `skills/deepmedchem/references/guidelines.md`'s checklist: confirm the target
  database actually supports the operation a prompt implies before building around it.

## Status of `examples/prompts/004_high_esp_low_tanimoto_rdkit.ipynb`

Resolved: Issue 2 turned out to be moot once RDKit (and pandas) were confirmed available to
the chatbot's runtime. Tanimoto is now computed locally with RDKit on the SMILES `deepmedchem`
already returned from an ESP-cosine search, instead of trying to get a second similarity metric
out of the API itself. Reporting Issue 2 to the operator as a capability gap is still worth
doing independently, but no longer blocks this example.

## Issue 4 — combining substructure search with a similarity score doesn't work

Found while finishing `005_logp_optimization.ipynb` (query: a thiazole-piperazine-linked
tricyclic dibenzoxazepine-imine, "modify everything except the tricycle, minimize logP, stay
shape-cosine-close to the query", on `freedom-space-5`).

- The full 5-ring exact-structure substructure query was rejected outright:
  `Interactive search capacity is currently full`. Smaller sub-fragments of the same query
  (individually, or combined 2 at a time) succeeded fine, so this is a query-complexity
  ceiling on the interactive substructure endpoint, not a general outage. There is no
  durable/batch alternative for substructure search — `Run` only supports `selection` and
  `selection_batch` kinds (confirmed in `deepmedchem/selection.py`'s `RunModel`).
- Even a simplified substructure query that matched fine (200 hits) can't be scored for
  shape-cosine similarity to the reference: intersecting that 200-hit substructure set with a
  200-hit shape-similarity search on the same query returned **zero overlap**. Same root cause
  as Issue 2 — no pairwise "score this specific molecule against a reference" endpoint exists,
  and a plain search response carries no fields from any other search.
- **Working alternative:** use the similarity search itself (`method="shape"`/`"esp"`/
  `"morgan"`) as the sole retrieval criterion when a real per-candidate score for that metric
  is needed. Verify what it structurally retains by checking hits locally with RDKit
  (`HasSubstructMatch`), rather than trying to build the wanted substructure constraint into a
  separate server-side query and reconcile the two afterward.
- **Also learned (from checking the actual synthons, not just search behavior):** a
  "retain X, vary the rest" prompt can be impossible for a structural reason that has nothing
  to do with the API — here, the tricycle and the piperazine turned out to be one indivisible
  synthon in `freedom-space-5`'s reaction scheme, not two separately combinable building
  blocks. No query design can make the piperazine vary in this specific database; only the
  substituent on its free nitrogen ever varies. Confirmed by a substructure query requiring
  only the tricycle (no piperazine in the query) still returning 200/200 hits with a full
  piperazine ring anyway.

## Issue 5 — `include_synthons=True` gives an exact alternative to substructure search, but
only via product-level search; there's no synthon-level search endpoint

Discovered while revisiting `005` after Issue 4: the user inspected `enamine-real-v5a`'s
actual synthons for this molecule (via the CHEESE web UI's synthon view) and found that,
unlike `freedom-space-5`, Enamine REAL's reaction scheme keeps the tricycle and the
piperazine+heteroaryl part as two **separate** synthons — so "retain the tricycle, vary
everything else" is genuinely possible in this database, just not through
`dmc.substructure()` (unsupported on `enamine-real-v5a`, see Issue 3).

- **`dmc.search(..., include_synthons=True)` works on `enamine-real-v5a`** (it's a similarity
  search, not the disabled substructure endpoint) and returns each hit's exact
  per-building-block decomposition: `{"slot": int, "synthon_id": str, "smiles": str}`, the
  SMILES carrying a `[U]` dummy atom at the attachment point. Confirmed live — for this query,
  slot 0 is the thiazole+piperazine synthon, slot 1 is the standalone tricycle
  (`[U]C1=Nc2ccccc2Oc2ccccc21`; note this database's version of the synthon lacks the
  chlorine from the original query — that substituent lives on the *other* building block in
  this particular reaction).
- Filtering hits by an **exact `synthon_id` match** is strictly more precise than an RDKit
  substructure check: it guarantees the literal identical building block, not just a
  structurally similar ring system.
- **Limitation:** only whatever ranks in a similarity search's top-K can be found this way —
  there is no "list every product built from synthon X" or synthon-level search endpoint.
  A single 200-hit shape search from the original query surfaced only 12 exact-synthon
  matches. Checked the raw request payloads sent by `search`/`search_cheese`/
  `search_substructure`/`sample` directly in `deepmedchem/client.py`: each is a small fixed
  JSON body with no hidden "search the synthon pool" option, and `dmc.catalog()` lists no
  synthon-level `database_id` separate from the seven product-level databases. The
  "Enamine REAL v5a (Synthon)" search mode visible in the CHEESE web UI does not appear to be
  exposed through the public `deepmedchem` package/API.
- **Working mitigation — neighbor expansion:** re-query using a few already-found
  exact-synthon matches as new reference SMILES. Since shape similarity is anchor-relative,
  each anchor's own top-200 surfaces a different slice of same-synthon molecules. 4 extra
  searches took the pool from 12 to 278 unique confirmed matches.
- **Honesty caveat this produces:** a candidate's shape-cosine score is only truly
  "similarity to the original query molecule" if it was found directly from that query's own
  search. Candidates found via an expansion anchor have a real score, just relative to that
  anchor instead (no pairwise "score this specific pair" endpoint exists to get the other
  number — same root cause as Issues 2 and 4). Report which reference each score is relative
  to; don't present them as uniformly comparable.

### Possible ask for the operator

A synthon-level search endpoint (search the synthon pool directly by shape/ESP/Tanimoto
similarity, then let the client reassemble/filter products) would remove the need for
neighbor expansion entirely and give much broader, more direct coverage. The CHEESE web UI
already has this ("Enamine REAL v5a (Synthon)" search); it just isn't in the public
`deepmedchem` package/API.

## Issue 6 — neighbor expansion (Issue 5's mitigation) can itself be badly biased

Found immediately after shipping the first synthon-matching version of `005`: the user
noticed every one of the 20 results still contained a piperazine, even though the query was
supposed to allow it to vary along with everything else.

- Root cause: neighbor expansion only re-queries using *already-found* exact-synthon hits as
  new anchors. If every hit found so far shares one reaction (e.g. all piperazine-based),
  every expansion anchor is also piperazine-shaped, so every further search stays anchored to
  molecules similar to piperazine-containing structures — the expansion can never escape the
  reaction it started in.
- This looked like solid evidence of a real constraint at first: checking `reaction_id` on
  the results showed only one (`rt_98c055f0a...`), and it stayed the same even when using an
  *independent* similarity metric (morgan/Tanimoto, a completely different fingerprint space
  from shape) with a fresh 200-hit search from the original query. Two different metrics
  agreeing looked like confirmation. It wasn't — both searches used the same
  piperazine-shaped molecule as the query, so both were subject to the same blind spot.
- **The actual test that mattered:** re-seed the search using the tricycle capped with
  something *other* than piperazine (e.g. a plain methylamino group, not derived from any
  found hit) as a brand new reference SMILES. This immediately surfaced a second `reaction_id`
  using the exact same tricycle synthon, with genuinely piperazine-free products (direct
  aryl/ether/methylene/amide linkages).
- **Fix:** don't rely solely on expanding from found hits. Seed the initial search with
  several deliberately varied cappings of the retained fragment (not just the original query)
  *before* doing any neighbor expansion, specifically to avoid anchoring the whole search to
  one structural neighborhood. Only expand further from hits once multiple distinct
  `reaction_id`s are already confirmed present.
- Result: the pool grew from 278 (1 reaction) to 1,315 (2 reactions, 1,090 + 225), and the
  lowest achievable logP dropped further, from 2.939 to 1.954, with piperazine-free
  structures dominating the lowest-logP end.
- **General lesson:** agreement between independent similarity metrics is not independent
  confirmation of anything if both were run from the same (or equivalent) query — it only
  rules out metric-specific bias, not query-anchoring bias. To check for the latter,
  deliberately vary the *reference molecule*, not just the scoring method.

## Status of `examples/prompts/005_logp_optimization.ipynb`

Done, rebuilt three times. Final version uses Enamine REAL (prompt originally said Freedom
Space 5; switched once synthon inspection showed Freedom Space 5 fuses tricycle+piperazine
into one indivisible synthon while Enamine REAL keeps them separate). Seeds the search with
several differently-capped tricycle queries (see Issue 6) plus exact-`synthon_id` filtering
and expansion (Issue 5) to build a 1,315-candidate pool spanning two distinct reactions
(piperazine-based and piperazine-free) guaranteed to retain the tricycle, transparently
labels which reference molecule and method each candidate's similarity score came from, and
picks the lowest-logP 20. logP dropped from 5.111 (starting molecule) to 1.954.
