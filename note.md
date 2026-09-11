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

## Status of `examples/prompts/006_vorasidenib_logp_umap.ipynb`

Done. Vorasidenib's SMILES pulled from PubChem (not memory) and cross-checked against its
known formula (C14H13ClF6N6); logP 4.312. Searched all 7 catalog databases by shape
(fetched dynamically from `dmc.catalog()`, not hardcoded — all 7 support `method="shape"`),
1,353 unique hits, 855 with lower logP than the starting molecule. Clustered 1-D on logP
(5 tiers via K-means) and separately ran UMAP on the full 8-descriptor RDKit set (mol_wt,
logp, tpsa, hbd, hba, rotatable_bonds, aromatic_rings, fraction_csp3), with the logp column
weighted 3x after z-scoring so it dominates the embedding without reducing UMAP to a
1-D sort (a single scalar can't drive a meaningful manifold projection on its own). Plotted
with a single-hue blue ordinal ramp (light=low logP, dark=high), validated with the
`dataviz` skill's palette checker. Needed `umap-learn` and `scikit-learn`, neither installed
by default — confirmed with the user these should be treated as available to the chatbot,
same status as RDKit/pandas.

## Issue 7 — IUPAC name → structure: the "Gold Book API" is the wrong tool; OPSIN is the
right one, and it needs no local installation at all

The user found the IUPAC Gold Book API
(https://iupac.github.io/WFChemCookbook/datasources/goldbook_api.html) and asked whether it
could turn a pasted systematic name into a SMILES for search. Checked the page directly:
**it doesn't** — it's a lookup service for chemistry *terminology definitions* (e.g. what
"cis-trans isomers" means), unrelated to name-to-structure conversion.

The actual right tool: **OPSIN** (Open Parser for Systematic IUPAC Nomenclature).

- OPSIN is normally a Java library. `pip install py2opsin` wraps it, but still shells out to
  a local `java` binary — confirmed this machine has none (`java -version` fails: "Unable to
  locate a Java Runtime"), and installing one would be a system-level change, not a project
  dependency, so it wasn't done.
- **No installation is needed at all**, because EBI hosts OPSIN as a free public REST API:
  `GET https://www.ebi.ac.uk/opsin/ws/{urlencoded_name}.{ext}`, `ext` one of `smi`, `inchi`,
  `stdinchi`, `stdinchikey`, `cml`, `png`, `svg`, or `json` (returns SMILES + InChI +
  InChIKey + CML together). A plain HTTP GET — `httpx` (already a `deepmedchem` dependency)
  is enough, no new package required.
- **Verified live, twice:**
  - `https://www.ebi.ac.uk/opsin/ws/benzene.json` → correct SMILES/InChI/InChIKey.
  - The full systematic name behind `005`'s original starting molecule
    ("2-methyl-N-[(1S)-2-(methylphenylamino)-2-oxo-1-(phenylmethyl)ethyl]-1H-indole-3-acetamide")
    round-tripped through OPSIN and matched, **exactly, stereocenter included**, the SMILES
    that had been hand-built earlier in the same session by manually parsing that name — a
    solid independent cross-check of both OPSIN and that earlier manual construction.
- **Integration notes for whoever builds this into the chatbot:** this is a live external
  network dependency (EBI's server), separate from the DeepMedChem API itself — needs its
  own error handling and shouldn't be assumed always reachable. OPSIN reports unparseable
  names via a `"status":"FAILURE"` field in the JSON body with a message, not necessarily a
  non-2xx HTTP status — check that field, don't rely on HTTP status alone.
- User is building `examples/prompts/007` around this themselves.

### Addendum — OPSIN's actual input scope (tested, not assumed)

Tested a range of name types against the live endpoint to pin down exactly what OPSIN
accepts, since "IUPAC name" is easy to over-scope:

| input | result |
|---|---|
| `2-(4-isobutylphenyl)propanoic acid` (fully systematic) | SUCCESS |
| `1,3,7-trimethylpurine-2,6-dione` (fully systematic) | SUCCESS |
| `toluene`, `cyclohexane`, `caffeine`, `acetylsalicylic acid`, `ibuprofen` (IUPAC-*retained* traditional names) | SUCCESS |
| `sodium chloride` (simple inorganic/ionic name) | SUCCESS |
| `aspirin` (common/trademark-derived name — **not** an IUPAC-retained name) | FAILURE |
| `vorasidenib` (WHO INN / generic drug name) | FAILURE |

The `aspirin` vs `acetylsalicylic acid` split is the important one: OPSIN's dictionary is
scoped to names IUPAC nomenclature itself formally retains, not general drug-name synonyms —
"aspirin" happens not to be one of those, "acetylsalicylic acid" is. **Practical routing
rule for the chatbot:** use OPSIN for systematic names and IUPAC-retained traditional names;
for trade/brand or INN/generic drug names, route to a name-search service instead (e.g.
PubChem's `/compound/name/{name}/property/...`, which is what was used for vorasidenib
earlier in this same investigation — it cross-references synonym databases OPSIN doesn't
have). Worth having the chatbot try OPSIN first and fall back to a name-search service on
`"status":"FAILURE"`, rather than picking one a priori.

## Issue 8 — an exact-synthon disconnection can genuinely not exist, even after
thorough searching (found while building 007)

Query: `(2S)-N-methyl-2-[[2-(2-methyl-1H-indol-3-yl)acetyl]amino]-N,3-diphenylpropanamide`
(via OPSIN — same molecule as `005`'s original starting compound, named from a different
parent this time: an N-acyl-phenylalanine-N-methylanilide). Prompt: fix the central
phenylalanine, vary the N-terminal (acyl group) and C-terminal (amide cap) independently, 10
examples each, on `enamine-real-v5a`.

- This exact molecule is a real Enamine REAL product (shape score 1.0 against itself).
  Its synthon decomposition (`include_synthons=True`) fuses phenylalanine **and the
  C-terminal amide** into one synthon, with the N-terminal acyl group as the separate,
  variable synthon — so "vary N-terminal, keep phenylalanine+C-terminal fixed" works
  directly via exact `synthon_id` filtering (41 matches from a single 200-hit shape search).
- The complementary disconnection needed for "vary C-terminal, keep phenylalanine+N-terminal
  fixed" was searched for directly and thoroughly: probed with several different concrete
  C-terminal caps, searched broadly from the free-acid intermediate (itself a real product)
  across two similarity metrics at the maximum limit — 400+ hits inspected in total. **Zero**
  instances of that decomposition turned up. Every reaction found either fuses
  phenylalanine+C-terminal (the pattern above) or leaves the C-terminus as a bare, uncapped
  acid.
- **Conclusion, reported as such rather than worked around:** Enamine REAL does not appear to
  offer a reaction that couples a fixed N-acyl-phenylalanine acid with a variable amine to
  form this specific class of C-terminal amide. This is a real gap in the available
  combinatorial chemistry for this exact building block, not a query-design problem — same
  category of finding as Issue 6, except here the broader search did *not* turn up a hidden
  second reaction. A structural-retention fallback (RDKit substructure check on similarity
  hits, not exact synthon identity) does find 29 plausible candidates, but per the user's
  instruction this was reported as infeasible rather than substituted in silently.
- **General lesson:** "keep searching more broadly until you find the reaction you need"
  (Issue 6's fix) is the right first move, but it can legitimately come up empty. Don't treat
  a broad, thorough search finding nothing as proof of a bug in the search — at some point
  it's real evidence the combinatorial space doesn't contain that disconnection, and the
  honest answer is to say so.

## Status of `examples/prompts/007_.ipynb`

Done. Structure obtained via OPSIN (ties directly to Issue 7); confirmed as an existing
Enamine REAL product (shape score 1.0); general structural-analogue search included per the
prompt's first ask. N-terminal variation (10 examples) delivered via exact-synthon matching.
C-terminal variation reported as infeasible for this exact building block/database (see
Issue 8), per the user's explicit choice not to substitute an approximate fallback.

## Issue 9 — SMILES → IUPAC name: no viable free option found; capability gap, not built

The reverse of Issue 7. Checked thoroughly, no shortcuts left untried:

| Tool | Verdict |
|---|---|
| RDKit | No such function exists — checked the `Chem` module directly for anything with "iupac" or "name" in it, nothing found. |
| OPSIN | Name→structure only, by design (its name literally means "parser of nomenclature") **and** confirmed empirically: feeding it a SMILES string as if it were a name returns `"status":"FAILURE"`. |
| PubChem `IUPACName` property | Lookup-only for compounds already registered in PubChem's database, not a live generator for arbitrary structures. Confirmed via the `/compound/smiles/.../property/IUPACName/JSON` POST endpoint: works for aspirin (`CID: 2244` → `"2-acetyloxybenzoic acid"`), returns `CID: 0` with no properties for a novel/unregistered structure. |
| STOUT (SMILES-TO-iUpac-Translator, the actual open-source ML tool built for this) | The right tool in principle, but heavy and fragile: pins `tensorflow==2.10.1` (an old version, likely to have Apple Silicon compatibility issues) **and** requires Java via `jpype1` (the same missing local dependency noted in Issue 7), plus a model download. No publicly hosted API exists to skip the install (checked both the PyPI page and the GitHub repo). |

**Conclusion:** treat SMILES→IUPAC-name generation as **not currently available** to the
chatbot. The realistic paths if it's ever needed are a commercial tool (ChemDraw's
"Structure to Name," ACD/Labs Name) or attempting the STOUT install in an isolated
environment (not the shared `deepmedchem` env, given the old TensorFlow pin risks breaking
other things) — neither was attempted here. This is the asymmetric counterpart to Issue 7:
name→structure is a solved, freely-available problem (OPSIN); structure→name is not.

## Issue 10 — PubChem's name-search API returns the SMILES under a different property
key than requested (found building 008)

Requesting `.../property/CanonicalSMILES,MolecularFormula/JSON` from PubChem's PUG-REST for
a name search (e.g. `lapatinib`) returns a response whose SMILES field is actually named
`ConnectivitySMILES`, not `CanonicalSMILES` — the requested key silently isn't present at
all, rather than erroring. Trusting the requested key name (`props["CanonicalSMILES"]`)
raises `KeyError` even though the request itself succeeds (HTTP 200, valid JSON, a real
CID). **Fix: read whichever key is present** —
`props.get("CanonicalSMILES") or props["ConnectivitySMILES"]` — rather than assuming the
requested property name is what comes back. Worth checking for the same pattern with any
other PubChem property request before trusting a hardcoded key name.

## Issue 11 — ranking by overall shape/ESP similarity silently conserves whichever
substituent dominates the pharmacophore, even when the prompt says "modify all decorations"

Found by the user after reviewing `008`'s first grid: every one of the top-25 results kept
lapatinib's chloro-fluorobenzyloxy-aniline branch completely unchanged, only varying the
other (sulfonamide) branch — despite the prompt saying "modify all its decorations."

- Not a bug in the query: this is what ranking by *overall* 3D similarity does by
  construction. One branch contributes more to the molecule's shape/electrostatics than the
  other, so changing it drops a candidate out of the high-shape+ESP pool faster than
  changing the smaller branch does. Quinazoline-retention alone doesn't prevent this — it
  only guarantees the ring system stays, not that *every* attached branch varies.
- **Fix:** decompose each candidate into its R-group fragments at the retained core with
  `Chem.ReplaceCore(mol, core_smarts, labelByIndex=True)` (then `Chem.GetMolFrags` to split
  them out), do the same for the original query molecule, and require that **none** of a
  candidate's fragments match the original's corresponding fragment — i.e. every branch
  must genuinely differ, not just "the molecule as a whole is different enough." This is a
  general, reusable technique for "vary all decorations off a retained core," not specific
  to lapatinib.
- Applied here: dropped the pool from 339 to 245 (confirmed live), and the resulting grid
  visibly varies both branches. Worth adding to `guidelines.md` as a general pattern:
  "modify all decorations" prompts need a per-branch difference check, not just core
  retention + a similarity ranking.

## Status of `examples/prompts/008_.ipynb`

Done, rebuilt once (see Issue 11). Lapatinib resolved via PubChem (OPSIN correctly failed
first, per the Issue 7 routing rule — it's an INN, not a systematic name). Searched all 7
databases; used shape+ESP intersection as the *correct* application of that technique here
(wanting both metrics high, not the Step 4 anti-pattern of wanting one low), with
quinazoline retention checked locally via RDKit rather than through `dmc.substructure()`
(sidesteps needing per-database substructure-search capability entirely). 339 analogues
found retaining quinazoline, retention rate varies sharply by database (0 for
`vast-2026-h2`, 146 for `d2b-spacem1`) — a real per-database chemistry difference, not a
bug. Then filtered to 245 requiring both branches to differ from lapatinib's (Issue 11). 5x5
grid of the top 25 by combined score (now visibly varying both branches); UMAP on ECFP4
fingerprints colored by that score (validated single-hue blue ramp); CSV export
(`id, smiles, vendor, email, price`) using `deepmedchem.ordering.procurement_contacts()`
for vendor/email, not invented values.

## Issue 12 — an unbracketed SMARTS atom matches more than the intended functional group;
and substructure search has a hard, deterministic 200-hit ceiling regardless of query phrasing

Found building `009` (query: "Xtalpi VAST" = `vast-2026-h2`, find molecules with a
tetrazole core and a benzyl group, Mw < 400 Da, logP < 4).

- **SMARTS false positive:** `Cc1ccccc1` (unbracketed `C`) was meant to mean "a benzyl
  group" but actually matches *any* non-aromatic carbon attached to a phenyl ring — an amide
  carbonyl carbon, a vinyl carbon, anything — because a plain SMARTS atom doesn't constrain
  hydrogen count/substitution beyond what's drawn (the same permissiveness noted for ring
  atoms in earlier notes, here biting on a simple substituent instead). Caught live: a real
  search hit, `O=C(NCC(F)F)c1cccc(-c2nnn[nH]2)c1`, matched the pattern despite having no
  benzyl group at all — the amide's carbonyl carbon satisfied it. **Fix:** use `[CH2]` (an
  explicit, exact hydrogen-count atom) instead of a bare `C` whenever the intent is
  specifically a methylene bridge, not "any carbon here." Validate any SMARTS fragment
  pattern against a known true-positive reference molecule *and* check a few actual live
  hits for false positives before trusting it — don't stop at the reference-molecule check
  alone (it would have passed here too, since losartan's benzyl is also a genuine `[CH2]`).
- **Two required fragments in one query:** SMARTS's dot syntax (`fragmentA.fragmentB`,
  disconnected components that must all be present somewhere in the target) works correctly
  against `dmc.substructure()` — confirmed live, all returned hits genuinely contained both
  fragments once the SMARTS itself was fixed.
- **200-hit ceiling is real and query-phrasing-independent:** this query returned exactly
  200 hits, and four differently-phrased but logically equivalent versions (reordering the
  two fragments, an "any-bond" generic tetrazole pattern, the 1H-tetrazole tautomer's SMARTS
  instead of the plain aromatic one) all returned the **exact same 200 molecules**, not a
  different sample. So for a query like this, 200 is the actual full retrievable population
  through the public substructure endpoint, not an artifact of how the query happened to be
  written — there's no way to "rephrase around" the cap the way neighbor expansion works
  around a similarity-search's top-K (Issue 5/6). Of those 200, 193 also satisfied the
  Mw/logP thresholds; reported as 193, not padded to the requested 200.

## Issue 13 — "benzyl group" was ambiguous, and the two readings give materially
different result sets; the chatbot should ask rather than pick one silently

The user's own follow-up after seeing the first `009` results: "benzyl cannot have any
other substituent - just CH2-Ph." The original SMARTS (`[CH2]c1ccccc1`) required the
bridging carbon to be a plain, unsubstituted CH2, but — per the usual SMARTS permissiveness
— still allowed the *ring* to carry substituents, since a plain lowercase `c` doesn't
constrain that either. Live results under the loose reading included methyl-, hydroxy-,
fluoro-, and bromo-substituted "benzyl" rings.

- **Fix:** require an explicit H on every non-attachment ring position:
  `[CH2]c1[cH][cH][cH][cH][cH]1`. Each `[cH]` demands exactly one hydrogen, which no further
  ring substituent can satisfy.
- Re-validating this stricter pattern caught a mistake in the *original* Issue 12 validation
  too: losartan (used there as the "benzyl-containing" reference) doesn't actually have a
  plain benzyl group — its CH2 connects to a biphenyl system (the attached ring is
  para-substituted by the second phenyl ring carrying the tetrazole), so it correctly fails
  the stricter pattern. Re-validated instead against benzylamine (`NCc1ccccc1`), a genuine
  plain-benzyl reference. Worth remembering generally: a molecule used to validate one
  reading of a pattern isn't necessarily a valid reference for a *stricter* reading of the
  same nominal group.
- Result count changed materially between readings: 193 of 200 satisfied Mw/logP under the
  loose "benzyl" reading, 165 of 200 under the strict one — a real, substantive difference
  in which molecules get returned, not a rounding difference.
- **General principle (the user's explicit ask):** when a prompt names a substituent or
  fragment in a way that has more than one reasonable chemical reading (here: "benzyl,"
  which could mean "any CH2 bridging to a phenyl-bearing carbon" or "CH2 bridging to an
  entirely unsubstituted phenyl") and the two readings produce materially different result
  sets, **the chatbot should ask a clarifying question rather than silently choosing one
  interpretation and running with it.** This is now documented as a behavioral requirement
  in `guidelines.md`, not just an API-capability note — it applies however the substructure
  ends up being expressed.

## Status of `examples/prompts/009_substructure_Mw_limited.ipynb`

Done, rebuilt once more (see Issue 13). Combined tetrazole+strict-unsubstituted-benzyl
substructure query on `vast-2026-h2` (200 hits, the confirmed ceiling), filtered locally to
165 satisfying Mw<400 Da and logP<4, 5x5 grid of a random 25 of those 165 (seeded for
reproducibility).
