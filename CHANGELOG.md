# Changelog

## Unreleased

### Added

- **Price limits.** `search(..., max_price=200)`, `sample(..., max_price=200)` (module, `Client`,
  `AsyncClient`), `Selection.max_price()` and `dmc search/sample --max-price 200` keep molecules
  whose estimated price (whole USD, 1 mg, US delivery) is at most the limit. The server applies
  the limit inside the synthon search and prices every returned molecule again; nothing is
  filtered on the client. Unpriced molecules are dropped unless `keep_unpriced=True` /
  `--keep-unpriced`. Needs a platform release whose catalog lists `max_price` under
  `pricing.constraints`.

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
  for unmetered service keys. `dmc status --verify` still checks the key but no longer prints
  `plan` or `credits_*`; use `dmc usage`.
- **`account_url` removed.** The `account_url` client argument, the profile field and the
  `DEEPMEDCHEM_ACCOUNT_URL` environment variable are gone, and `dmc status` no longer prints
  `account_url`. Existing `config.toml` files that still contain `account_url` load normally;
  the key is ignored and dropped the next time the config is saved.

`dmc order` still recognises the vendor contacts for CSVs exported from the CHEESE web UI, so
existing exports of these catalogues can still be turned into quote requests.
