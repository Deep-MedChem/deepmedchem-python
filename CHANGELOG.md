# Changelog

## Unreleased

### Breaking changes

The SDK now talks only to the DeepMedChem platform API (`/api/v2/...`). Classic CHEESE Search
(`api.cheese.deepmedchem.com`) will be deprecated soon, and nothing in the package calls it any
more.

- **Classic CHEESE catalogues removed.** The enumerated and in-stock catalogues added in 0.3.0b5 (#33, #40)
  are gone: ENAMINE-REAL, ENAMINE-AA, CHEMSPACE-5B-RO5, CHEMSPACE-5B-BEYOND-RO5,
  CHEMSPACE-5B-FREEDOM, EXPLORE-ENUMERATED, EXPLORE-DIVERSE, EXPLORE-5P4B, SYNPLE-4B, XTALPI,
  MOLECULE-ONE, CHEMRIYA, MCULE-IN-STOCK, MCULE-FULL, MCULE-AA, MCULE-AA-IN-STOCK,
  MCULE-UNNATURAL-AA, MCULE-UNNATURAL-AA-IN-STOCK, MOLPORT, CHEMSPACE-SCREENING and ZINC15.
  Passing one of these IDs or its shorthand (for example `mcule-in-stock`) to `search`,
  `search_cheese`, `search_substructure`, `sample`, `search_many`, `Selection.from_database`,
  `optimize`, or the matching `dmc` commands raises `ValueError` before any request is sent.
  Other unknown or private database IDs still pass through to the platform API unchanged.
- **`catalog()` / `dmc databases` list the platform catalog only.** They no longer merge in
  `/available_databases_full` or emit the `RuntimeWarning` when CHEESE Search is unreachable.
- **Usage removed.** `Client.usage()`, `AsyncClient.usage()`, `deepmedchem.usage()`,
  `deepmedchem.aio.usage()`, the `Usage` model and `dmc usage` are gone, because the platform
  API has no usage endpoint yet. `dmc status --verify` still checks the key but no longer prints
  `plan` or `credits_*`. The CHEESE web app shows the daily credit balance.
- **`account_url` removed.** The `account_url` client argument, the profile field and the
  `DEEPMEDCHEM_ACCOUNT_URL` environment variable are gone, and `dmc status` no longer prints
  `account_url`. Existing `config.toml` files that still contain `account_url` load normally;
  the key is ignored and dropped the next time the config is saved.

`dmc order` still recognises the vendor contacts for CSVs exported from the CHEESE web UI, so
existing exports of these catalogues can still be turned into quote requests.
