# `dmc` command reference

The `dmc` command is installed with `pip install deepmedchem` and is also available as
`deepmedchem`. Run `dmc <command> --help` for the authoritative flags of the installed version.

Global behaviour:

- Every command that talks to the API accepts `--profile NAME` (default: the active profile,
  or `DEEPMEDCHEM_PROFILE`) and `--json` to print the raw API response instead of a table.
- Exit code is non-zero on API errors; the message includes the error `code` and, when
  available, the `request_id`.
- Credentials resolve in this order: `DEEPMEDCHEM_API_KEY`, the profile's OS-keyring entry,
  the `credentials.json` fallback file next to the SDK config. Force one store with
  `DEEPMEDCHEM_CREDENTIAL_STORE=keyring|file`.
- `DEEPMEDCHEM_API_URL` overrides the API host (default `https://api.deepmedchem.com`).

## Authentication

| Command | Purpose |
| --- | --- |
| `dmc login [--profile P] [--no-browser] [--token-stdin] [--timeout S]` | Device login. Prints an approval code and URL, opens a browser when a display exists, waits for approval, stores the key. `--token-stdin` reads an existing key from stdin instead. |
| `dmc status [--verify] [--json]` | Shows the active profile, API URL, and credential store. `--verify` makes one request to confirm the key is accepted. |
| `dmc logout [--profile P]` | Removes the locally stored key for the profile. |
| `dmc usage [--json]` | Plan, daily CHEESE Credit limit, used, remaining, reset time, and any active promotion. |

## Discovery

| Command | Purpose |
| --- | --- |
| `dmc databases` (alias `dmc catalog`) | Lists every searchable database with its `database_id`, size, whether per-compound prices are published, and the vendor email for quotes and orders. `--json` returns the full catalog document including releases and capability limits. |

Use the `database_id` column verbatim as the `-d/--database` value below.

## Searching

### `dmc search SMILES -d DATABASE [options]`

Ranked similarity search for one query molecule.

| Flag | Meaning |
| --- | --- |
| `-d, --database ID` | Required database id. |
| `-m, --method {morgan,shape,esp}` | `morgan` (default) is ECFP4 Tanimoto; `shape` and `esp` are CHEESE 3D shape and electrostatic similarity. |
| `-n, --limit N` | Number of hits (default 20). |
| `-o, --output FILE` | Save hits; format inferred from `.csv`, `.sdf`, `.smi`, `.json`. |
| `--format {csv,sdf,smi,json}` | Output format when it cannot be inferred. |

### `dmc substructure QUERY -d DATABASE [options]`

Exact substructure match. Returns matches, not ranked neighbours.

| Flag | Meaning |
| --- | --- |
| `-f, --format-in {smiles,smarts}` | Query language, default `smarts`. Use `smiles` for a concrete fragment such as `CNC(=O)N1CCC1`. |
| `-n, --limit N` | Number of hits (default 100). |
| `--timeout-seconds S` | Server-side search budget (default 30). Raise for recursive or wildcard-heavy SMARTS. |
| `-o, --output FILE`, `--format` | As for `search`. |

### `dmc sample -d DATABASE [options]`

Random molecules from a space, useful for baselines and property distributions.

| Flag | Meaning |
| --- | --- |
| `-n, --count N` | Number of molecules (default 100). |
| `--seed S` | Reproducible sampling. |
| `-o, --output FILE`, `--format` | As for `search`. |

### Output

The table shows rank, score (similarity searches only), price estimate, and SMILES. Exports and
`--json` carry every field: `product_id`, `reaction_id`, `database_id`, `database_release`,
`score`, `metric`, `price`, and vendor specific extras. SDF export needs
`pip install "deepmedchem[sdf]"`; CSV, SMILES, and JSON have no extra dependency.

Prices are non-binding estimates in whole US dollars for delivery to the United States and are
absent (`-` or `null`) for databases whose vendor publishes none.

## Ordering and quotes

### `dmc order RESULTS.csv [options]`

Turns an exported results CSV into vendor-ready request bundles. It never sends email or places
an order.

| Flag | Meaning |
| --- | --- |
| `--get-quote` | Ask vendors to confirm price and availability instead of initiating an order. |
| `-d, --database ID` | Database id when the CSV has no `database`/`database_id` column. |
| `--output-dir DIR` | Where to write the per-vendor directories (default: next to the CSV). |
| `--to ADDRESS` | Send everything to one address, for private databases without a configured contact. |
| `--amount-mg MG` | Requested amount per molecule. |
| `--name NAME` | Name used in the email closing. |
| `--no-open` | Only write files; do not open a mail client (use over SSH). |

For each vendor the command writes `email.txt` (the draft) and `molecules.csv` containing only
database id, a `-DMCH` reference id, and SMILES. Scores, properties, and SDK price estimates are
deliberately left out. DeepMedChem is CCed so the vendor can attribute the request.

## Typical session

```bash
dmc login
dmc usage
dmc databases
dmc search "O=C(Nc1ccccc1)c1ccncc1" -d enamine-real-v5a -m shape -n 50 -o shape_hits.csv
dmc substructure "[#6]C(=O)N[c;R]" -d enamine-real-v5a -n 200 -o amides.csv
dmc order shape_hits.csv --get-quote --amount-mg 5 --no-open
```
