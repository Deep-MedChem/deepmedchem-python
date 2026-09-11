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
for the specific database named in a prompt before assuming it supports what you need. This
is a capability that can change: `enamine-real-v5a` previously reported
`search_substructure: False` (a confirmed limitation as of 2026-09-10, see `note.md` Issue
3), but as of 2026-09-11 all 7 catalog databases report `search_substructure: True`. Always
check live rather than trusting a cached assumption (including this document) about what a
given database supports.

Separately — and unaffected by the above — `dmc.substructure()` enforces a query-complexity
ceiling on the interactive endpoint regardless of which database supports the operation in
principle (Step 8, `note.md` Issue 4/14): a query built from several fused rings can be
rejected outright as too costly (`Interactive search capacity is currently full`) even on a
database whose catalog capability says substructure search is supported. "Does this database
support substructure search" and "is this particular query small enough for the interactive
endpoint" are two separate checks — confirm both before relying on `dmc.substructure()` for a
given prompt.

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
and a correctness caveat

When `dmc.substructure()` isn't viable for a "retain this fragment, vary the rest" prompt —
either the database doesn't support it, or (Step 7) the query is too complex for the
interactive endpoint even on a database that does — check whether the retained fragment is
its own standalone synthon: `dmc.search(..., include_synthons=True)` works regardless of
substructure support (it's a similarity search, not the substructure endpoint) and returns
each hit's exact per-building-block decomposition — `{"slot": int, "synthon_id": str,
"smiles": str}`, with a `[U]` dummy atom marking the attachment point.

**Correctness caveat, confirmed live:** exact-`synthon_id` matching is *not* equivalent to a
structural-retention check, and can produce false negatives. It only finds hits that share
one specific reaction's building-block boundary — if the same final structural pattern can
also arise from a different reaction/disconnection in that database, exact-synthon matching
will miss it entirely. Confirmed while rebuilding example `007`: exact-synthon matching found
zero instances of a particular fixed-fragment pattern even after inspecting 400+ hits, but a
plain RDKit structural check (explicit-hydrogen SMARTS, `[cH]` on every ring position that
must stay unsubstituted — see Step 11/12) found matches immediately from the very same
search. Prefer `dmc.substructure()` when query complexity allows it (Step 7), or a local
RDKit structural check on similarity-search hits (Step 8) otherwise; reach for exact-synthon
matching only when the question genuinely is "was this specific building block used," not as
a general substitute for "does this structure exist."

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

## Step 10 — "Modify all decorations off a core" needs a per-branch check, not just
core retention plus a similarity ranking

Retaining a core substructure (Step 8/9) guarantees the ring system stays; it does **not**
guarantee every attached branch actually varies. Ranking candidates by overall similarity
(shape, ESP, Tanimoto — any of them) will silently conserve whichever branch contributes
more to that similarity score, even across hundreds of results, because changing that branch
drops a candidate out of the high-similarity pool faster than changing a less-influential
branch does. A prompt asking to "modify all decorations" or vary multiple substituent
positions independently needs an explicit per-branch difference check, not just "core
retained + high similarity."

The check: `Chem.ReplaceCore(mol, core_smarts, labelByIndex=True)` followed by
`Chem.GetMolFrags(..., asMols=True)` splits a molecule into its R-group fragments at the
retained core. Do this for the original query and for each candidate, and keep only
candidates where **none** of the fragments match the query's corresponding fragment — i.e.
every branch position genuinely differs. (Found live: a lapatinib-analogue search that
retained its quinazoline core and ranked by shape+ESP kept one branch identical across every
one of the top 25 results, until this check was added.)

## Step 11 — an unbracketed SMARTS atom matches more than the intended functional group

A plain SMARTS atom (`C`, `N`, ...) doesn't constrain hydrogen count or substitution beyond
what's explicitly drawn — it matches "at least this," not "exactly this." Writing
`Cc1ccccc1` to mean "a benzyl group" actually matches *any* non-aromatic carbon attached to
a phenyl ring: an amide carbonyl carbon, a vinyl carbon, anything. Confirmed live — that
pattern matched `O=C(NCC(F)F)c1cccc(-c2nnn[nH]2)c1`, a molecule with no benzyl group at all,
because the amide's carbonyl carbon satisfied it. Use an explicit hydrogen count (`[CH2]`)
whenever the intent is a specific group like a methylene bridge, not "any atom here."

Validating a fragment SMARTS against one known true-positive reference molecule is
necessary but not sufficient — a reference molecule's own instance of the group is often
exactly the well-formed case the pattern is too permissive around, so it passes the
reference check and still produces false positives elsewhere. Also spot-check a few actual
live search hits for whether they contain the intended group, not just whether they match
the pattern.

Note also that a molecule validated as a true positive for one reading of a group isn't
necessarily valid for a *stricter* reading of the same nominal group — losartan validated
"contains a benzyl-like CH2-aryl linkage" but turned out not to have a plain, unsubstituted
benzyl group at all (its CH2 connects to a biphenyl system, para-substituted by a second
ring), so it correctly fails a stricter "no ring substitution allowed" version of the same
pattern. Re-check the reference molecule itself when tightening a definition, don't assume
it still applies.

## Step 12 — When a substituent/fragment name has more than one reasonable chemical
reading, ask — don't silently pick one

Chemical shorthand in a prompt ("benzyl group," "quinazoline core," "phenyl ring") often has
more than one defensible reading, and different readings can produce materially different
result sets — not a rounding difference, a different set of molecules. Confirmed live:
"benzyl group" read loosely (a CH2 bridging to any phenyl-bearing carbon, substituents on
the ring allowed) vs. strictly (CH2 bridging to a completely unsubstituted phenyl) changed
which molecules passed a property filter by a large margin (193 vs. 165 out of the same 200
structural matches) — genuinely different chemistry, not noise.

**When translating an ambiguous substituent/fragment description into a query, the correct
behavior is to ask a clarifying question before committing to an interpretation**, rather
than silently choosing the more permissive (or any other) reading and presenting the result
as if it were the only possible one. This applies whether the query ends up expressed as
SMARTS, an exact-synthon match, or anything else — the ambiguity is in the chemistry the
prompt names, not in how it gets encoded. Reasonable signals that a term needs
disambiguating rather than a best-guess default: the term names a common substructure that
chemists routinely draw both substituted and unsubstituted (benzyl, phenyl, tolyl...); the
prompt doesn't explicitly say whether substitution is allowed; and a quick check shows the
two readings actually diverge on real data (as above), not just in principle.

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
- [ ] Does the prompt ask to vary *multiple* substituent positions off a retained core?
      Check each branch individually with `Chem.ReplaceCore` (Step 10) — ranking by overall
      similarity alone will silently conserve whichever branch matters most to that score.
- [ ] Any SMARTS fragment meant to mean a specific group (e.g. a methylene bridge) uses an
      explicit hydrogen count (`[CH2]`), not a bare atom — and was spot-checked against a
      few live hits for false positives, not just a reference molecule (Step 11).
- [ ] Does a named substituent/fragment have more than one reasonable chemical reading
      (substituted vs. unsubstituted, which tautomer, etc.)? If the readings would actually
      produce different result sets, ask the user rather than silently picking one (Step 12).
