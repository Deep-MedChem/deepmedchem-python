# Changelog

## 0.4.0b4 (2026-10-21)

- Add the `onepot` alias for Onepot CORE 2.0 Lite and supplier information: 1 mg pricing ($250 for two components; $375 for three or more), estimated synthesis time and success, and US delivery timing.
- Preserve the source reaction and ordered vendor synthon IDs in Onepot order drafts addressed to `hello@onepot.ai`.
- Expose supplier offers alongside search and sampling results for reproducible ordering. Library availability remains determined by the live server catalog.

## 0.4.0b3 (2026-10-16)

### Added

- Per-database optimization capability discovery through synchronous and asynchronous
  `client.optimizations.capabilities(database)`, including release pins and supported ADMET units.
- Native ADMET optimization examples: supported constraints and objectives guide CP16
  acquisition and receive final full-product teacher validation; custom docking stays in
  the external scoring callback. Endpoint availability depends on the selected deployment
  and database.
- Native optimization constraints and objectives, streaming results, and campaign resume
  support from the intervening development updates.

## 0.4.0b2 (2026-10-13)

### Added

- **Price limits.** `search(..., max_price=200)`, `sample(..., max_price=200)` (module, `Client`,
  `AsyncClient`), `Selection.max_price()` and `dmc search/sample --max-price 200` keep molecules
  whose estimated price (whole USD, 1 mg, US delivery) is at most the limit. The server applies
  the limit inside the synthon search and prices every returned molecule again; nothing is
  filtered on the client. Unpriced molecules are dropped unless `keep_unpriced=True` /
  `--keep-unpriced`. Needs a platform release whose catalog lists `max_price` under
  `pricing.constraints`.

## 0.4.0b1 (2026-10-13)

### Breaking changes

The SDK now talks only to the DeepMedChem platform API (`/api/v2/...`). Classic CHEESE Search
(`api.cheese.deepmedchem.com`) will be deprecated soon, and nothing in the package calls it any
more.

- **Classic CHEESE catalogues removed.** The enumerated and in-stock catalogues added in 0.3.0b5 (#33, #40)
  are no longer routed to CHEESE Search: ENAMINE-REAL, ENAMINE-AA, CHEMSPACE-5B-RO5,
  CHEMSPACE-5B-BEYOND-RO5, CHEMSPACE-5B-FREEDOM, EXPLORE-ENUMERATED, EXPLORE-DIVERSE,
  EXPLORE-5P4B, SYNPLE-4B, XTALPI, MOLECULE-ONE, CHEMRIYA, MCULE-IN-STOCK, MCULE-FULL, MCULE-AA,
  MCULE-AA-IN-STOCK, MCULE-UNNATURAL-AA, MCULE-UNNATURAL-AA-IN-STOCK, MOLPORT,
  CHEMSPACE-SCREENING and ZINC15. Their IDs (in any case, e.g. `MCULE-IN-STOCK` or
  `mcule-in-stock`) now go to the platform API casefolded, like any other database ID, because
  the platform API will serve several of them under those IDs; this SDK reaches each one as soon
  as it does. Until then, `search`, `search_cheese`, `search_substructure`, `sample`,
  `search_many`, selections, runs and the matching `dmc` commands raise
  `deepmedchem.RetiredDatabaseError` (a `DeepMedChemError` and a `ValueError`, code
  `retired_database`) when the platform API answers that it does not serve the database. These
  rejected requests cost no credits. `optimize` reports the platform's own "not offered for
  optimizations" error. Other unknown or private database IDs still pass through unchanged.
- **`catalog()` / `dmc databases` list the platform catalog only.** They no longer merge in
  `/available_databases_full` or emit the `RuntimeWarning` when CHEESE Search is unreachable.
- **Usage now comes from the platform API.** `Client.usage()`, `AsyncClient.usage()`,
  `deepmedchem.usage()`, `deepmedchem.aio.usage()` and `dmc usage` read the new
  `GET /api/v2/usage` on the `api_url` host instead of classic `/rate-limit/status`. The balance is
  the same account-wide daily CHEESE Credit ledger, and reading it costs no credits. The `Usage`
  model follows the platform's snake_case wire format: `plan`, `limit`, `used`, `remaining`,
  `unlimited`, `window`, `usage_day`, `reset_at`, `seconds_to_reset`, `user_id`. The camelCase
  aliases (`resetAt`, `secondsToReset`, `userId`) and the `promo` block (`UsagePromotion`) are
  gone, and `dmc usage --json` prints the platform response. `limit` and `remaining` are `None`
  for unmetered service keys. A server that predates the endpoint (404) raises
  `DeepMedChemError` with code `usage_unavailable`. `dmc status --verify` still checks the key but no longer prints
  `plan` or `credits_*`; use `dmc usage`.
- **`account_url` removed.** The `account_url` client argument, the profile field and the
  `DEEPMEDCHEM_ACCOUNT_URL` environment variable are gone, and `dmc status` no longer prints
  `account_url`. Existing `config.toml` files that still contain `account_url` load normally;
  the key is ignored and dropped the next time the config is saved.

`dmc order` still recognises the vendor contacts for CSVs exported from the CHEESE web UI, so
existing exports of these catalogues can still be turned into quote requests.

### New features

- **Virtual subsets and SMARTS filters.** `dmc search` and `dmc sample` (and `search`/`sample`
  in Python) take `--subset NAME` (or `-d DATABASE:SUBSET`), `--where`, `--require`,
  `--require-count`, `--exclude` and `--explain`. Filters become one molecule selection sent to
  `/api/v2/selections`, so the server checks every result. `dmc subsets` lists the built-in
  subsets: amino acids, building blocks, warheads, ro5, ro3 and more.
- **Navigator optimization (beta).** `client.optimizations` and `dmc.optimize()` / `dmc optimize`
  drive resumable ask/tell optimizations of your own score over a chemical space, on accounts
  where the platform enables them.
- **Many query molecules in one run.** `Client.search_many()` runs each query as an item of one
  durable batch run and returns per-query hits in input order.
- **HTML export.** `SearchResult.to_html()` and `-o hits.html` save a self-contained page with
  2D structures.

### Fixes

- Browser login on the `dev` profile uses the dev CHEESE web app.
