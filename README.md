# DeepMedChem Python SDK

[![PyPI](https://img.shields.io/pypi/v/deepmedchem?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/deepmedchem/)
[![Python](https://img.shields.io/pypi/pyversions/deepmedchem?style=flat-square&logo=python&logoColor=white)](https://pypi.org/project/deepmedchem/)
[![CI](https://img.shields.io/github/actions/workflow/status/Deep-MedChem/deepmedchem-python/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/Deep-MedChem/deepmedchem-python/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)
[![Production API](https://img.shields.io/badge/API-api.deepmedchem.com-0A7EA4?style=flat-square)](https://api.deepmedchem.com/api/v2/docs)
[![Documentation](https://img.shields.io/badge/docs-docs.deepmedchem.com-4B32C3?style=flat-square)](https://docs.deepmedchem.com/)

The official, chemistry-thin Python client for the DeepMedChem hosted chemical-space platform.
It contains no RDKit, models, databases, or proprietary search implementation.

> **Beta:** `deepmedchem` 0.4 is available for early use. APIs may still change before the
> stable release.

## Installation

```bash
pip install deepmedchem
```

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

## Command line

The `dmc` command (also installed as `deepmedchem`) covers the everyday operations without
writing Python:

```bash
dmc databases                        # abbreviations, sizes, prices, order emails
dmc databases --detailed             # full IDs, BioSolveIT mappings, availability, links
dmc usage                            # account plan and CHEESE Credits remaining today
dmc search "CC(=O)Oc1ccccc1C(=O)O" -d enamine -m shape -n 10
dmc search "CC(=O)Oc1ccccc1C(=O)O" -d enamine -o aspirin.csv
dmc substructure "[N;R0][N;R0]C(=O)" -d enamine -n 10 --timeout-seconds 45 -o hydrazides.sdf
dmc search "CC(=O)Oc1ccccc1C(=O)O" -d synple --subset amino-acids
dmc sample -d freedom -n 100 --seed 7 -o sample.smi
dmc order aspirin.csv --get-quote
dmc optimize run my-run -d enamine --minimize --score-cmd './dock.sh {input} {output}'
dmc optimize status                  # your optimizations; also ask, tell, results, cancel
```

`databases` lists every searchable space with its size, whether per-compound price estimates
are available, and the vendor address for orders and quotes. Output captured on September 11, 2026;
the catalog can change. For Enamine, the size is an estimated number of source reagent
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

Searches print a table of rank, similarity score, price, and SMILES, followed by what was
searched and the score range. Substructure hits show `exact` instead of a score, and samples
have no score column. Product ids and the other API fields are kept in `--json` and in exports.

Example output from the production API (September 11, 2026):

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

`-o/--output` saves the hits as CSV, SDF, SMILES (`.smi`), or JSON, inferred from the file suffix
(`--format` overrides it). CSV and SDF carry the score, price, product id, and every other field
from the response. SDF output needs RDKit (`pip install "deepmedchem[sdf]"`); the other formats
have no extra dependencies. Every command accepts `--json` for the raw API response and
`--profile` to pick a configured profile.

### Enamine filtered population

The production and development APIs use the filtered Enamine release `2026-09-06.2` by default for
`database="enamine"`, including sampling, similarity, selections, runs, and substructure search.
It applies MW ≤500, logP ≤5, HBA ≤10, HBD ≤5, rotatable bonds ≤10, and TPSA ≤140 Å²
on the assembled product after selecting the first valid source topology. Price estimates
remain available on the returned products.

The updated estimate is **93.41B** source combinations (95% interval: 93.19–93.63B),
based on 18.7 million draws. This is the historical approximately 95B population,
replacing the larger 336.72B normalized population as the default.
The catalogue reports the estimate and its confidence interval. The CLI marks
estimated counts with `~`; this population counts source reagent combinations, which may
produce the same molecular graph. Sampling is not uniform over unique molecular graphs.
No client-side property preset is needed to select this population.

```python
from deepmedchem import Client

with Client() as client:
    sample = client.sample(database="enamine", count=20, seed=7)
    matches = client.search_substructure("C(=O)N", database="enamine", limit=20)
```

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

[^python-availability]: All rows are Make-On-Demand spaces served by the platform API with its full feature set: similarity, substructure search, sampling, selections and runs.

[^price-estimates]: Prices are planning estimates per compound, before any discounts are applied, and generally assume a low number of molecules ordered and price per 1 mg. For instance VAST H2 2026 uses 1 mg per compound in a 50-compound order. Other databases similarly assume small orders; the exact basis varies by vendor. Request a final quote for your actual quantity and order size. Counts and price support come from the live API catalog.

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

## Quickstart

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

Example output from the production API (September 11, 2026):

```text
SearchResult(3 molecules, method='shape', database='enamine-real-v5a')
1  score=0.9726  price=$245  O=C(O)Oc1ccccc1C(=O)O
2  score=0.9719  price=$163  COC(=O)Oc1ccccc1C(=O)O
3  score=0.9551  price=$163  COC(=O)Oc1ccccc1C(C)=O
```

Prices are estimates in whole US dollars for delivery to the United States. They are returned in
the original search response, so both `hit.price` and the aligned `result.prices` list are
available without another API request. Databases without price estimates return `None`; run
`dmc databases` for the current list and the vendor address to request a binding quote.

## Optimize with explicit chemistry and optional custom scores

Put supported structures and property limits in `constraints=` / `properties=`, and supported
preferences in `objectives=`. Navigator guides synthon tuple generation and ranking, then
verifies assembled products. Docking and proprietary models remain callbacks.

```python
import deepmedchem as dmc

result = dmc.optimize(
    database="enamine", budget=500, batch_size=50, name="qed-amino-acids",
    properties={"MolWt": (None, 400), "MolLogP": (0, 3)},
    constraints={"structures": {"all": [{"pattern_id": "alpha-amino-acid/v1"}]}},
    objectives=[{"type": "property", "property_id": "rdkit.qed",
                 "direction": "maximize", "lower": 0, "upper": 1}],
)
result.to_csv("qed_demo.csv")
```

No callback or client RDKit installation is needed for built-in-only runs. Check
`dmc.catalog()["optimization"]` for the deployment's enabled constraints, objectives and ADMET
endpoints. Typed structure requirements use synthon anchors, including reaction junctions;
released descriptor caches and reaction margins guide property-feasible sampling. Final product
checks enforce counts, chirality, exclusions and exact descriptor bounds. The engine reports
its actual acquisition path and any fallback in `optimization.engine.constraint_acquisition`.

For docking, keep the callback focused on docking:

```python
result = dmc.optimize(docking_score, direction="minimize", database="enamine",
                      budget=500, batch_size=50, properties={"MolWt": (None, 400)})
```

A callback takes a SMILES list and returns one score per molecule, optionally with metrics.
`name=` resumes an unchanged specification; local score journals prevent repeated external
scoring after an interrupted upload. The sync/async create APIs support the same parameters.
The CLI accepts `--constraints constraints.json` and `--objectives objectives.json`; omit
`--score-cmd` for built-in-only runs. See [optimization examples](examples/optimization/README.md)
for normalized mixed objectives, molecular similarity, ADMET and docking.

ADMET availability is specific to the database and its installed release. With a client,
inspect `client.optimizations.capabilities("enamine")`; the returned
`predicted_properties` maps endpoint IDs to units. The equivalent catalog field is
`optimization.database_capabilities["enamine-real-v5a"].predicted_properties`.
The legacy `optimization.constraints.predicted_properties` contains only endpoints
shared by every advertised database, so it can be empty while Enamine supports ADMET.
Also check the catalog's `constraints.enabled` and `objectives.enabled` flags.

```python
with dmc.Client() as client:
    capabilities = client.optimizations.capabilities("enamine")
    endpoint = "openadmet-herg-pchembl"
    units = capabilities["predicted_properties"][endpoint]
    result = dmc.optimize(
        client=client,
        database="enamine",
        direction="maximize",  # aggregate desirability
        budget=100,
        batch_size=20,
        constraints={"predicted_properties": [
            {"endpoint_id": endpoint, "operator": "lte", "value": 6, "units": units},
        ]},
        objectives=[
            {"type": "predicted_property", "endpoint_id": endpoint, "units": units,
             "direction": "minimize", "lower": 3, "upper": 8},
        ],
    )
```

The bounds above illustrate the API; choose thresholds for your experiment. CP16
ranks synthon tuples before assembly. The matching full-product model supplies the
final eligibility check and objective value. These are model predictions, not
experimental measurements. A database with no advertised endpoint rejects that term
before a campaign is created. Adding an external docking term keeps docking in the
callback while the same internal ADMET guidance and checks remain active.



## Filters and virtual subsets

A similarity search can be restricted to a *virtual subset*: a definition rather than a
stored catalogue, so it works on every DeepMedChem chemical space. No enumerated
amino-acid version of Synple exists, yet this returns only amino acids:

```bash
dmc search "CC(C)C[C@H](NC(=O)c1ccccc1)C(=O)O" -d synple --subset amino-acids
dmc search "CC(C)C[C@H](NC(=O)c1ccccc1)C(=O)O" -d synple:amino-acids        # same search
dmc search "CC(=O)Oc1ccccc1C(=O)O" -d enamine --subset ro5 --where "tpsa=40..90"
dmc search "CC(=O)Oc1ccccc1C(=O)O" -d enamine --require "c1ccncc1" --exclude "[N+](=O)[O-]"
dmc search "CC(=O)Oc1ccccc1C(=O)O" -d enamine --require-count 1-2 "[NX3;H2]"
dmc search "CC(=O)Oc1ccccc1C(=O)O" -d enamine --max-price 200                # USD per 1 mg
dmc search "CC(=O)Oc1ccccc1C(=O)O" -d synple:amino-acids --explain           # no credit used
dmc sample -d enamine --subset druglike -n 100
dmc subsets                                                               # the registry
```

`--subset`, `--where`, `--require`, `--require-count` and `--exclude` can be repeated and
are combined with AND. `--where` takes `mw`, `logp`, `hbd`, `hba`, `rotb`, `tpsa`, `fsp3`
and `arom` (or the `rdkit.*` property IDs) as `mw<=450`, `logp<4`, `tpsa=40..90` or
`1<=hbd<=3`. `--max-price USD` keeps molecules whose catalogue price (whole USD, 1 mg, US
delivery, the `price` column) is at most that amount; molecules without a price are dropped
unless `--keep-unpriced` is given. `--explain` prints the selection document with the
server's validation and execution estimate, and runs nothing.

| subset | definition |
|---|---|
| `amino-acids` | alpha-amino-acid residue, carboxyl free or esterified; N free, alkylated, acylated, carbamate-protected, sulfonylated or in a ring |
| `amino-acid-building-blocks` | alpha-amino acid with N free, alkylated or carbamate-protected (Boc, Fmoc, Cbz) |
| `beta-amino-acids` | beta-amino-acid residue, carboxyl free or esterified |
| `carboxylic-acids`, `primary-amines`, `acrylamides` | the named group |
| `covalent-warheads` | any of acrylamide, chloroacetamide, vinyl sulfonamide, sulfonyl fluoride, propiolamide |
| `ro5` | Lipinski: MW <= 500 Da, cLogP <= 5, HBD <= 5, HBA <= 10 (server preset `lipinski-ro5/v1`) |
| `druglike` | MW <= 500, cLogP -10..5, HBA <= 10, HBD <= 5, RotB <= 10, TPSA <= 140, Fsp3 0.1..0.8, 1..5 aromatic rings |
| `beyond-ro5` | MW > 500 Da |
| `ro3` | MW <= 300, cLogP <= 3, HBD <= 3, HBA <= 3, RotB <= 3 |

`amino-acids` has the scope of the Mcule amino-acid catalogues: it matches 98-100% of
each residue file of MCULE-AA (2026-08-01) except glycine and alanine (about 90%), whose
remaining entries attach the residue through an aromatic ring nitrogen. Matching ignores
stereochemistry, so natural and unnatural (D) amino acids are not told apart.
`dmc subsets --json` prints every SMARTS.

In Python the same filters are keyword arguments of `search` and `sample`:

```python
import deepmedchem as dmc

hits = dmc.search(query, database="synple", subset="amino-acids", limit=50)
hits = dmc.search(query, database="enamine:ro5", where=["mw<=450"], require=["c1ccncc1"])
hits = dmc.search(query, database="enamine", require=[("[NX3;H2]", 1, 2)])  # 1-2 matches
picks = dmc.sample(database="enamine", subset="druglike", count=100, seed=7)
cheap = dmc.search(query, database="enamine", max_price=200)  # price <= $200 per 1 mg
```

A filtered search is one `molecule-selection/1` document: a similarity objective plus
the filters as constraints, sent to `POST /api/v2/selections` and returned as a
`SelectionResult`. `Selection.subset()`, `.require_structure()`, `.exclude_structure()`,
`.require_any_structure()`, `.where("mw<=450")` and `.max_price(200)` build the same
constraints by hand.
The server checks every constraint on every assembled product, so each returned
molecule satisfies all filters; nothing is filtered on the client. Engines that support
it anchor the synthon search on synthons able to carry a required pattern instead of
filtering nearest neighbours. If fewer molecules than requested pass the filters among
the candidates examined, the result is shorter and carries a `partial_results` warning,
which the CLI prints.

Limits: a filtered search returns at most 200 hits. Sampling accepts property filters
only, because the API applies structure constraints to similarity searches alone.
Batch search (`--input`) and substructure search do not take filters yet. The API does
not report how many molecules a subset contains.

A price limit is enforced inside the synthon search, not by dropping expensive hits from an
unfiltered list: the server skips building-block combinations priced above the limit before
it ranks them, then prices every returned molecule again. It works for similarity search
and sampling on databases whose catalog entry lists `max_price` under `pricing.constraints`
(`dmc databases --json`); elsewhere the server answers `capability_unavailable` for
`constraints.price`. A limit below the cheapest price of a database returns no molecules
and a `price_unattainable` warning. Prices are planning estimates, not quotes. With
RDKit installed (`pip install "deepmedchem[rdkit]"`), SMARTS are checked locally before
any request.

## SMILES and SMARTS substructure search

Enamine substructure search is enabled in production on release `2026-09-06.2`.
All 326 route partitions have native indexes. Returned products satisfy the filtered
population rules and are checked against the original query, including bond order,
recursive predicates, and chirality.

Use `format="smiles"` for a concrete molecular graph, including the existing
junction-spanning examples. Use `format="smarts"` for atom lists, ring constraints,
recursive expressions, and other SMARTS query features:

```python
junction = dmc.substructure("CNC(=O)N1CCC1", format="smiles", database="enamine-real-v5a", limit=10)
hydrazides = dmc.substructure(
    "[N;R0][N;R0]C(=O)", format="smarts", database="enamine-real-v5a",
    limit=10, timeout_seconds=45, timeout=60,
)
```

See the runnable [substructure example](examples/docs/substructure_search.py) for several
SQC-derived SMARTS queries. More demanding motifs can take tens of seconds.
`timeout_seconds` sets the server search budget; `timeout` sets the SDK's HTTP timeout
and should leave room for serialization and transport. A deadline can return fewer
than `limit` hits; inspect `result.raw["timed_out"]`, coverage, and warnings. Exact
returned matches do not imply an exhaustive search of every possible product.

Any result writes itself with `result.to_csv(path)`, `result.to_sdf(path)`, or
`result.to_file(path)` (format inferred from the suffix). `dmc.usage()` and `Client.usage()` return
the account plan and today's CHEESE Credit balance (`plan`, `limit`, `used`, `remaining`,
`unlimited`, `reset_at`, `seconds_to_reset`) from `GET /api/v2/usage`. The balance is shared by all
of the account's keys and the CHEESE web app, resets at midnight UTC, and reading it is free.

Module-level `search`, `substructure`, `sample`, `catalog`, and `usage` operations create and close
a small internal client. The explicit `Client` remains available for connection reuse and advanced
selections/runs. Search results behave as ordered SMILES sequences (`result[0]`, `result[:3]`,
`list(result)`) while retaining typed hits, scores, prices, metadata, warnings, and the complete raw
response locally.

The default profile calls `https://api.deepmedchem.com`. Keys created at
`https://cheese.deepmedchem.com` work on both the legacy and v2 APIs. All keys for an account share
one daily CHEESE Credit balance: one successful synchronous execution or durable-run item costs one
credit. Ordinary synchronous work has a 10-second execution limit; use the Runs API
for longer work, where a basic item has a 60-second limit. Substructure search uses
its separate `timeout_seconds` budget.

Credentials resolve from an explicit `api_key`, `DEEPMEDCHEM_API_KEY`, compatibility environment
variables, a custom credential provider, the selected profile's OS-keyring entry, or the
`credentials.json` fallback file. Set `DEEPMEDCHEM_CREDENTIAL_STORE=file` or `=keyring` to force one
store. Use `dmc login --profile dev` for the development service; profiles never share
credentials. Dev login opens `https://cheese-dev.deepmedchem.com`, reusing your session
in that browser. If an older `config.toml` explicitly sets `[profiles.dev].web_url` to
`https://cheese-new-dev.deepmedchem.com`, change it to `https://cheese-dev.deepmedchem.com`.
Explicit profile URL overrides are preserved.

Every request identifies its source with `X-DMC-Client`, `X-DMC-Client-Version`, and
`X-DMC-SDK-Version`. The default values attribute direct SDK use to `deepmedchem-python`; an
application such as Navigator can override `application` and `application_version` while retaining
the installed SDK version separately.

## Selections and durable runs

`Selection` and `Run` are immutable, chemistry-thin builders. They produce the public
`molecule-selection/1` and `run/1` documents; all chemistry and capability validation remains on
the API.

Exact RDKit constraints, fast predicted-property acquisition, and hard assembled-product predicted
ranges are available through `Selection`. The simple `search` and `sample` helpers and the CLI
accept the common filters directly; see *Filters and virtual subsets*.

```python
from deepmedchem import Client, Selection

selection = (
    Selection.from_database("enamine-real-v5a")
    .reference(
        "query",
        smiles="CCOc1ccc(C(=O)N2CCN(C)CC2)cc1",
    )
    .maximize_similarity("rdkit.ecfp4_tanimoto", reference="query")
    .require_preset("lipinski-ro5/v1")
    .where("rdkit.mol_wt", lte=450, units="Da")
    .acquire_predicted_property(
        "openadmet-herg-pchembl",
        direction="minimize",
        keep_fraction=0.25,
    )
    .include("properties", "objective_components")
    .limit(100)
)

with Client() as dmc:
    result = dmc.selections.create(selection)
```

Every returned RDKit value is calculated on the assembled product and enforced literally. For
predicted-property acquisition, factorized CP16 scores cheaply narrow the candidate pool before
assembly and the pinned OpenADMET teacher predicts every unique surviving assembled product. The
response exposes the two stages separately as `hit.acquisition.approximate_value` and
`hit.acquisition.predicted_value`. These remain model predictions rather than assay measurements.

A hard predicted-property range is enforced only by the assembled-product teacher:

```python
selection = (
    Selection.from_database("enamine-real-v5a")
    .reference("query", smiles="CC(=O)Oc1ccccc1C(=O)O")
    .maximize_similarity("rdkit.ecfp4_tanimoto", reference="query")
    .where_predicted_property(
        "openadmet-herg-pchembl",
        lte=5.0,
        units="pChEMBL",
    )
    .limit(20)
)
```

Property-filtered random sampling uses the same selection contract without acquisition:

```python
selection = (
    Selection.from_database("freedom-space-5")
    .sample(seed=42)
    .require_preset("lipinski-ro5/v1")
    .where("rdkit.mol_wt", lte=450, units="Da")
    .include("properties")
    .limit(100)
)
result = Client().selections.create(selection)
```

The authenticated catalog is the source of truth for each database's available properties,
presets, predicted-property endpoints, and supported acquisition operation.

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


### Seeded optimization and analog harvesting

`client.optimizations.create(..., hit_threshold=-7, start_paused=True)` initializes without
proposing. After status becomes `paused`, use `run.add_seeds(rows, mode="synthon")` with measured
scores and Navigator product IDs, or `mode="external"` with SMILES and scores. SMILES-only seeds
train the surrogate; in-space product seeds also initialize analog search. Neither spends the
scoring budget. If a scorer was pinned, pass the same `scorer` metadata with seeds.

After the seed operation finishes, `run.resume()` starts proposals. During an ask/tell loop,
`run.transition("analog_harvest_accurate", idempotency_key="harvest-1")` queues a switch after
scoring the current batch. `analog_harvest_fast` is also supported. Poll `run.refresh()` and inspect
`resource.raw["pending_operation"]`; controls apply asynchronously and do not replace pending
molecules. Invalid seeds pause the run with a reason and preserve its checkpoint.

`dmc optimize seeds NAME seeds.json --mode synthon --idempotency-key seeds-1` and
`dmc optimize transition NAME analog_harvest_fast --idempotency-key harvest-1` expose the same
operations. `dmc optimize run` and `dmc.optimize()` accept a hit threshold (`--hit-threshold` /
`hit_threshold`). See [the seed example](examples/optimization/05_seed_and_harvest.py).

Custom `properties={"MolWt": (None, 350)}` windows are checked on assembled products by the
upgraded worker, including when combined with `filters="druglike"`. Check the catalog's
optimization property capabilities before requesting them.

### Long-running optimizations

Give each independent campaign a name and pin scorer metadata when reproducibility matters.
The scoring callback executes locally; keep one coordinating client per campaign.

```python
result = dmc.optimize(
    score, direction="maximize", name="campaign-1", budget=50_000, batch_size=500,
    score_chunk_size=25, score_workers=2, result_limit=0,
)
# After interruption, resume using the stored specification (no new job is created):
result = dmc.resume("campaign-1", score, score_chunk_size=25, result_limit=0)

with dmc.Client() as client:
    run = client.optimizations.get("campaign-1")
    run.export_csv("observations.csv")
    # Optional: pip install 'deepmedchem[parquet]'
    run.export_parquet("observations.parquet")
    for observation in run.iter_results(order="best", limit=100):
        print(observation)
```

`score_chunk_size` journals each finished scoring chunk before the next upload. A crash can
still lose an unfinished chunk; work on another machine needs the same local cache to reuse
unsubmitted scores. Changing chunk size on resume is supported. `score_workers` runs callbacks
in bounded threads and requires a thread-safe scorer. Running callbacks finish before thread
shutdown; this is not a mechanism for killing a hung docking process.

`result_limit=0` returns an `OptimizationResult` with an empty observations list and the server
summary in `.optimization`; a positive value fetches only the best N observations. Omitting it
preserves the existing behavior of collecting every observation. Streaming exports are atomic
and encode arbitrary metrics in one JSON column instead of discovering all metric columns in
memory. Async handles also support `iter_results()`.

`run.pause()` requests a pause after the current server step; `run.resume()` releases a server
pause. `dmc.resume(id_or_name, score)` also resumes a server-paused job before continuing scoring.
For built-in-only objectives, use `dmc.resume(id_or_name)` without a callback.
These pause controls require the corresponding backend release. Cancelled jobs remain terminal.
A local advisory lock rejects a second high-level scoring client sharing the same cache; it
cannot coordinate clients on different machines. Use one coordinator with scoring threads for
one run, or different campaign names for independent jobs.

New backend result pages pin the snapshot at the first page so advancing campaigns do not
shuffle later pages. With older servers, export completed or paused jobs for a stable view.
