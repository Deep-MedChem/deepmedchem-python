# Translating a prompt into a doable request

A natural-language request often names a goal that sounds simple but isn't directly
expressible as one DeepMedChem API call. Before writing code — as an example, or as a
chatbot answering a live request — check feasibility first. Guessing at the literal
wording and discovering the mismatch only after execution wastes a durable run, or worse,
ships a result that looks plausible but silently answers a different question than the
one asked.

## Step 1 — Find the one dominant retrieval criterion

Every query against a combinatorial/enumerated database needs exactly one thing driving
*which candidates get generated or retrieved*: a single similarity objective, a substructure
pattern, a sampling distribution, or a set of hard constraints. If a prompt names two
retrieval criteria that pull in different directions (see Step 4), only one of them can
actually drive retrieval — the other has to become a report-only or post-processing step.

## Step 2 — Similarity objectives are maximize-only

`Selection.maximize_similarity(metric_id, reference=...)` only accepts `direction="maximize"`.
This was confirmed against the live API, not just the SDK: hand-constructing a payload with
`"direction": "minimize"` gets rejected server-side with `Input should be 'maximize'`. There is
no way to ask the API to retrieve the *least* similar molecules by a given metric. A prompt
asking to "minimize" or find the "lowest" similarity cannot be satisfied as a literal
objective — it has to be reframed (see Step 4).

Valid `metric_id`s for a similarity objective (confirmed live): `rdkit.ecfp4_tanimoto`,
`cheese.shape`, `cheese.electrostatic`. Two objectives can be declared on the same
`Selection` and both validate, but there is no `weight` field — you cannot make one
objective dominant and the other purely informational at the API level.

## Step 3 — Optimize vs. filter vs. report are different mechanisms

- **Optimize** (drives which candidates get returned/ranked): `maximize_similarity`,
  `acquire_predicted_property`.
- **Filter** (hard, exact, pass/fail on the assembled product): `.where(property_id, ...)`,
  `.require_preset(...)`, `.where_predicted_property(...)`. These only accept catalog
  `property_id`s (RDKit descriptors like `rdkit.mol_wt`) or predicted-property endpoint ids —
  never a similarity-to-reference metric. There is no way to threshold on Tanimoto or ESP
  cosine via `.where()`.
- **Report only** (returned for inspection, does not by itself affect selection):
  `.include("properties", "objective_components", ...)`. A metric only appears here if it
  was already declared as an objective — asking to "include" Tanimoto without declaring it
  as an objective will not compute it.

There is no "compute the similarity of these two specific molecules" endpoint. The only way
to get a metric's value for a specific candidate is for that metric to be an objective (or
the search method) that generated or ranked that candidate.

## Step 4 — Anti-pattern: don't intersect two independent "most similar" retrievals

A tempting-looking workaround for "find X that is dissimilar by metric A but similar by
metric B" is to run two separate top-K searches (one per metric) and intersect the hit
sets. **This cannot work and is not just contradictory, it's structurally wrong**: a
molecule that is genuinely dissimilar by metric A will never appear in a top-K-most-similar-
by-A list, by definition. Intersecting two "most similar" lists can only ever surface
molecules that are similar by *both* metrics — the opposite of what was asked.

(We made exactly this mistake building an ESP/Tanimoto scaffold-hopping example: intersecting
top-200 ESP hits with top-200 Tanimoto hits and picking the lowest-Tanimoto ones from that
intersection returned molecules with Tanimoto 0.42–0.7 — a biased, moderately-high-similarity
set, not genuinely dissimilar molecules.)

The correct reframing: pick the one metric that should drive retrieval (Step 1), retrieve
that list, and get the second metric's value for those *same* candidates either by declaring
it as a second objective in the same query (Step 2/3 caveats apply) or by accepting that it
cannot be obtained for arbitrary already-retrieved molecules with this API and saying so.

## Step 5 — Check the execution tier before finalizing an example

Call `client.selections.estimate(validation.normalized_selection)` and read
`execution_tier`. One similarity objective against a database's current release is often
`"synchronous"`; two objectives (or certain other combinations) force `"durable"`, which
must go through `Run.selection(...)` / `client.runs.create(...)`. This is a hard release-
level capability, not something to guess — check it live, per database and per release,
before writing the "reference" call in an example.

## Step 6 — Validate live before treating anything as ground truth

`client.selections.validate(selection)` is cheap and safe (no candidates generated, no
durable execution) and will immediately surface an invalid `metric_id`, a rejected
`direction`, or a malformed constraint with a precise server error message. Always validate
before estimating, and always estimate before creating — especially before committing code
to a notebook meant to be a graded reference for chatbot testing.

## Step 7 — Not every database supports the same operations

Database capabilities vary — check `dmc.catalog()["libraries"]` (or `dmc databases --json`)
for the specific database named in a prompt before assuming it supports what you need.
Confirmed live: `enamine-real-v5a` has `search_substructure: False` — `dmc.substructure()`
simply does not work there, even though it fully supports `search`/`search_cheese` and
`Selection`. `freedom-space-5`, `cheminfinita-2026-02`, `synple-explore-2025-10`,
`synple-synple-2025-10`, and `vast-2026-h2` do support substructure search. If a prompt names
a specific database, verify that database's capability block matches what the query needs
before writing any code around it — don't assume parity across databases just because they're
all combinatorial/enumerated chemical spaces.

## Step 8 — Substructure search and a similarity score don't combine, and "retain X" can be
impossible for reasons that have nothing to do with the API

A prompt can ask to retain a fragment via substructure search *and* report/rank by a
similarity score (e.g. shape or ESP cosine) to the same reference. These don't combine:
a substructure-search candidate set cannot be scored for similarity to a reference —
confirmed live, intersecting a 200-hit substructure search with a 200-hit similarity search
on the same query returned zero overlap (same root cause as Step 4: no pairwise "score this
molecule against a reference" endpoint). Complex multi-ring substructure queries can also be
rejected outright as too costly for the interactive endpoint (`Interactive search capacity is
currently full`) even when nowhere near a timeout, and there is no durable/batch alternative
for substructure search — `Run` only supports `selection`/`selection_batch` kinds.

Working alternative when a real per-candidate similarity score is required: use that
similarity search itself (`method="shape"`/`"esp"`/`"morgan"`) as the sole retrieval
criterion, then check locally with RDKit (`HasSubstructMatch`) what its hits structurally
retain, rather than trying to enforce the retained fragment server-side and reconcile two
separate result sets afterward.

Separately: if a "retain fragment X, vary the rest" query keeps returning the same
supposedly-variable part unchanged no matter how the query is loosened, the cause may be the
combinatorial library itself, not the query — X and the "rest" can be one indivisible synthon
in that database's reaction scheme, not two separately combinable building blocks. Confirmed
in one case by checking the actual synthons: a substructure query requiring only the fragment
meant to be retained (with no constraint at all on the part meant to vary) still returned
every hit with that "variable" part unchanged. No query design fixes this — say so, rather
than assuming the query needs more loosening.

## Step 9 — Exact synthon matching: a substructure-search alternative, with a coverage caveat

When a database doesn't support `dmc.substructure()` (Step 7) but a prompt still needs
"retain this fragment, vary the rest," check whether that fragment is its own standalone
synthon: `dmc.search(..., include_synthons=True)` works even on databases without
substructure support (it's a similarity search, not the disabled endpoint) and returns each
hit's exact per-building-block decomposition — `{"slot": int, "synthon_id": str, "smiles":
str}`, with a `[U]` dummy atom marking the attachment point. Filtering hits by an *exact*
`synthon_id` match is stronger than an RDKit substructure check: it guarantees the literal
same building block, not just a similar-looking ring system.

Two things to check before relying on this:

- **Is the fragment actually separable in this database?** Different databases decompose the
  same scaffold differently. Confirmed live: for one tricyclic+piperazine scaffold,
  `freedom-space-5` fuses both into one indivisible synthon (no query can vary the piperazine
  there — see Step 8), while `enamine-real-v5a` keeps them as two separate synthons for the
  same molecule. Check the actual decomposition per database before assuming either way.
- **Coverage is capped by whatever a similarity search's top-K surfaces** — there is no
  "list every product built from synthon X" endpoint, and none of the request payloads sent
  by `search`/`search_cheese`/`search_substructure`/`sample` accept a synthon-level search
  parameter (`dmc.catalog()` also lists no synthon-level `database_id`). A single top-200
  similarity search may only turn up a handful of exact-synthon matches. **Neighbor
  expansion** helps: re-run the same search using a few already-found exact-synthon matches
  as new reference SMILES — since similarity ranking is anchor-relative, each anchor's own
  neighborhood surfaces a different slice of same-synthon molecules (in one case, 4 extra
  searches took 12 matches to 278). But this means only hits found directly from the
  *original* query carry a similarity score that's actually "vs. the original query" — hits
  found via an expansion anchor have a real score, just relative to that anchor. Report which
  reference each score is relative to; don't present them as uniformly comparable.

**Pitfall: neighbor expansion can be badly biased, and cross-checking with a second metric
doesn't catch it.** Expanding only from already-found hits can never escape the reaction/
neighborhood those hits came from — if everything found so far shares one `reaction_id`,
every expansion anchor does too, so the search stays trapped there. This can look like a real
structural constraint: checking a second, independent similarity metric (e.g. morgan
alongside shape) from the *same* query will often agree, since both are anchored to the same
molecule — agreement between metrics only rules out metric-specific bias, not query-anchoring
bias. The actual test is to seed the search with a deliberately *different* reference molecule
(e.g. the retained fragment capped some other way, not derived from any hit found so far)
before concluding a variable part isn't actually varying. In one case this surfaced a second
reaction using the identical retained synthon, with a class of products (piperazine-free
analogues) that pure neighbor expansion never found across hundreds of hits.

## Checklist

- [ ] What is the *single* criterion actually driving candidate retrieval?
- [ ] Any other named criteria — are they optimizable (objective), filterable (`.where`/
      preset/predicted-property), or report-only, and does the API support that combination?
- [ ] Does anything ask for "minimize", "least", "lowest", or "most different" on a
      similarity metric? That's infeasible as a direct objective — reframe or say so.
- [ ] Would satisfying this require intersecting two independent "most similar" retrievals?
      If so, stop — reframe using Step 4's guidance instead.
- [ ] Sync or durable? Confirmed via `estimate()`, not assumed.
- [ ] Any post-processing (sorting, secondary selection) uses only fields the API actually
      returned, or values computed locally with RDKit on SMILES the API already returned
      (confirmed available alongside pandas in the chatbot's runtime) — never a value
      invented or approximated without a real computation behind it.
- [ ] Does the prompt need both a substructure-retained candidate set *and* a similarity
      score to the same reference? These don't combine (Step 8) — use the similarity search
      as the sole retrieval criterion and check retention locally with RDKit instead.
- [ ] If "retain X, vary the rest" keeps returning the "rest" unchanged no matter how the
      query is loosened, check whether X and the rest are one indivisible synthon in that
      database — no query fixes that, say so instead (Step 8).
- [ ] If substructure search isn't available on the target database, check whether the
      retained fragment is its own synthon and use exact `synthon_id` matching plus neighbor
      expansion instead (Step 9) — but label which candidates have a real score vs. the
      original query vs. an expansion anchor.
- [ ] If the prompt, taken literally, is not achievable with this API, say so plainly and
      propose the closest achievable reformulation — don't silently substitute a different
      query and present it as satisfying the original request.
- [ ] Does the specific database named in the prompt actually support the operation needed
      (e.g. substructure search)? Check `dmc.catalog()`, don't assume.
