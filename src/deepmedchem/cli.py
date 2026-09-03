"""Standard-library CLI for DeepMedChem: authentication, catalog, searches, and usage."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from typing import Any

from . import __version__
from .auth import LoginError, browser_login, can_open_browser
from .client import Client, DeepMedChemError
from .config import (
    FILE_STORE,
    CredentialError,
    config_path,
    credentials_path,
    delete_all_api_keys,
    delete_api_key,
    get_stored_api_key,
    load_config,
    resolve_profile,
    save_api_key,
)
from .export import FORMATS, infer_format, write_result
from .models import SearchResult, Usage
from .ordering import open_order_drafts, prepare_order, procurement_contacts

SEARCH_METHODS = ("morgan", "shape", "esp")
# Typical make-on-demand delivery time quoted by every vendor in the catalog.
DELIVERY_TIME = "3-6 weeks"


# --- Presentation helpers ----------------------------------------------------


def _format_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    align_right: Sequence[bool] | None = None,
) -> str:
    """Render a plain, monospace table with a single header rule."""

    cells = [[("" if value is None else str(value)) for value in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in cells:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    right = list(align_right or [False] * len(headers))

    def render(row: Sequence[str]) -> str:
        parts = []
        for index, value in enumerate(row):
            width = widths[index]
            parts.append(value.rjust(width) if right[index] else value.ljust(width))
        return "  ".join(parts).rstrip()

    lines = [render(list(headers)), "  ".join("-" * width for width in widths)]
    lines.extend(render(row) for row in cells)
    return "\n".join(lines)


def _human_count(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    for suffix, scale in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if number >= scale:
            return f"{number / scale:.1f}{suffix}"
    return f"{int(number)}"


def _price(value: int | None) -> str:
    return f"${value}" if value is not None else "-"


def _score(value: float | None) -> str:
    return f"{value:.4f}" if value is not None else "-"


def _date(value: str | None) -> str:
    """Trim an ISO timestamp to its calendar date for compact display."""

    if value and len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    return value or "?"


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return "?"
    hours, rest = divmod(max(int(seconds), 0), 3600)
    minutes = rest // 60
    return f"{hours}h {minutes:02d}m"


def _print_database_table(catalog: dict[str, Any]) -> None:
    libraries = catalog.get("libraries") or []
    contacts = procurement_contacts()
    rows = []
    for library in libraries:
        database_id = str(library.get("database_id") or "")
        contact = contacts.get(database_id.casefold())
        pricing = library.get("pricing") or {}
        rows.append(
            [
                database_id,
                _human_count(library.get("product_count")),
                "yes" if pricing.get("available") else "-",
                contact.email if contact else "-",
            ]
        )
    print(
        _format_table(
            ["database", "molecules", "prices", "orders"],
            rows,
            align_right=[False, True, False, False],
        )
    )
    print()
    print(f"{len(rows)} databases, made on demand and delivered in {DELIVERY_TIME}.")
    print("Order or request quotes by email, or run `dmc order results.csv`.")


def _catalog_entry(client: Client, database_id: str | None) -> dict[str, Any] | None:
    """Look up the catalog record of ``database_id`` (name, size); ``None`` when unavailable."""

    if not database_id:
        return None
    try:
        catalog = client.catalog()
    except DeepMedChemError:
        return None
    wanted = database_id.casefold()
    for library in catalog.get("libraries") or []:
        if str(library.get("database_id") or "").casefold() == wanted:
            return library
    return None


def _result_summary(result: SearchResult, library: dict[str, Any] | None) -> list[str]:
    """Short, human sentences about what was searched and how the hits scored."""

    meta = result.meta
    name = (library or {}).get("name") or meta.database or "the database"
    size = _human_count((library or {}).get("product_count")) if library else "-"
    space = f"{size} molecules ({name})" if size != "-" else str(name)
    elapsed = f" in {meta.elapsed_ms:.0f} ms" if meta.elapsed_ms is not None else ""
    count = len(result)
    if meta.method == "sample":
        return [f"Random sample of {count} from {space}."]
    if meta.method == "substructure":
        return [f"Searched {space}{elapsed}.", f"{count} exact substructure matches returned."]
    lines = [f"Searched {space}{elapsed}."]
    scores = [score for score in result.scores if score is not None]
    if scores:
        metric = meta.metric or meta.method or "similarity"
        lines.append(f"Similarity range: {min(scores):.2f}-{max(scores):.2f} {metric}.")
    return lines


def _print_result_table(result: SearchResult, *, library: dict[str, Any] | None = None) -> None:
    method = result.meta.method
    if method == "substructure":
        headers = ["rank", "match", "price", "smiles"]
        rows = [[hit.rank, "exact", _price(hit.price), hit.smiles] for hit in result.hits]
        right = [True, False, True, False]
    elif method == "sample":
        headers = ["rank", "price", "smiles"]
        rows = [[hit.rank, _price(hit.price), hit.smiles] for hit in result.hits]
        right = [True, True, False]
    else:
        headers = ["rank", "score", "price", "smiles"]
        rows = [[hit.rank, _score(hit.score), _price(hit.price), hit.smiles] for hit in result.hits]
        right = [True, True, True, False]
    print(_format_table(headers, rows, align_right=right))
    print()
    for line in _result_summary(result, library):
        print(line)


def _print_usage(usage: Usage) -> None:
    print(f"plan:      {usage.plan or 'unknown'}")
    if usage.unlimited:
        print(f"credits:   unlimited ({usage.used:,} used today)")
    else:
        remaining = "?" if usage.remaining is None else f"{usage.remaining:,}"
        limit = "?" if usage.limit is None else f"{usage.limit:,}"
        print(f"credits:   {remaining} of {limit} remaining today ({usage.used:,} used)")
        if usage.reset_at:
            print(f"resets:    {usage.reset_at} (in {_duration(usage.seconds_to_reset)})")
    promo = usage.promo
    if promo is not None and promo.label:
        extra = []
        if promo.multiplier:
            extra.append(f"{promo.multiplier:g}x")
        if promo.base_limit is not None:
            extra.append(f"base {promo.base_limit:,}/day")
        if promo.ends_at:
            extra.append(f"until {_date(promo.ends_at)}")
        print(f"promo:     {promo.label} ({', '.join(extra)})")


# --- Argument parsing ----------------------------------------------------------


def _add_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", help="Named profile (default: active profile)")
    parser.add_argument("--api-url", help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help="Print the raw JSON response")


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="Save results to FILE (.csv, .sdf, .smi, or .json; inferred from the suffix)",
    )
    parser.add_argument(
        "--format",
        choices=FORMATS,
        help="Output format when it cannot be inferred from --output",
    )
    parser.add_argument("--include-synthons", action="store_true", help=argparse.SUPPRESS)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dmc",
        description="Search DeepMedChem chemical spaces from the terminal.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    login = commands.add_parser("login", help="Save a CHEESE API key securely")
    login.add_argument("--profile")
    login.add_argument("--no-browser", action="store_true")
    login.add_argument("--token-stdin", action="store_true")
    login.add_argument("--timeout", type=int, default=600)

    status = commands.add_parser("status", help="Show profile and authentication status")
    status.add_argument("--profile")
    status.add_argument("--verify", action="store_true")
    status.add_argument("--json", action="store_true")

    logout = commands.add_parser("logout", help="Remove locally stored credentials")
    logout.add_argument("--profile")
    logout.add_argument("--all", action="store_true")

    usage = commands.add_parser("usage", help="Show account plan and remaining daily credits")
    _add_connection_options(usage)

    databases = commands.add_parser(
        "databases", aliases=["catalog"], help="List searchable databases and their pricing"
    )
    _add_connection_options(databases)

    search = commands.add_parser("search", help="Similarity search for a SMILES query")
    search.add_argument("smiles", help="Query molecule as SMILES")
    search.add_argument("-d", "--database", required=True, help="Database id, see `databases`")
    search.add_argument(
        "-m", "--method", choices=SEARCH_METHODS, default="morgan", help="Similarity method"
    )
    search.add_argument("-n", "--limit", type=int, default=20, help="Number of hits")
    search.add_argument("--shortlist-multiplier", type=int, default=10, help=argparse.SUPPRESS)
    _add_output_options(search)
    _add_connection_options(search)

    substructure = commands.add_parser("substructure", help="Exact SMILES/SMARTS substructure")
    substructure.add_argument("query", help="Substructure query")
    substructure.add_argument("-d", "--database", required=True, help="Database id")
    substructure.add_argument(
        "-f",
        "--format-in",
        choices=("smiles", "smarts"),
        default="smarts",
        dest="query_format",
        help="Query language (default: smarts)",
    )
    substructure.add_argument("-n", "--limit", type=int, default=100, help="Number of hits")
    substructure.add_argument(
        "--timeout-seconds", type=int, default=30, help="Server-side search budget"
    )
    _add_output_options(substructure)
    _add_connection_options(substructure)

    sample = commands.add_parser("sample", help="Draw random molecules from a database")
    sample.add_argument("-d", "--database", required=True, help="Database id")
    sample.add_argument("-n", "--count", type=int, default=100, help="Number of molecules")
    sample.add_argument("--seed", type=int, help="Reproducible sampling seed")
    _add_output_options(sample)
    _add_connection_options(sample)

    order = commands.add_parser(
        "order", help="Prepare vendor email drafts from a DeepMedChem results CSV"
    )
    order.add_argument("input", metavar="RESULTS.csv", help="Search results CSV")
    order.add_argument(
        "--get-quote",
        action="store_true",
        help="Ask vendors to confirm price and availability instead of initiating an order",
    )
    order.add_argument(
        "-d",
        "--database",
        help="Database ID when the input CSV has no database/database_id column",
    )
    order.add_argument("--output-dir", metavar="DIR", help="Directory for request artifacts")
    order.add_argument("--to", help="Override the vendor recipient (combines all rows)")
    order.add_argument("--cc", default="info@deepmedchem.com", help=argparse.SUPPRESS)
    order.add_argument("--amount-mg", type=float, help="Requested amount per molecule")
    order.add_argument("--name", help="Name used in the email closing")
    order.add_argument(
        "--no-open", action="store_true", help="Create files without opening an email client"
    )
    return parser


# --- Commands -----------------------------------------------------------------


def _profile(args, config) -> str:
    selected = resolve_profile(args.profile, config)
    config.profile(selected)
    return selected


def _open_client(args) -> Client:
    config = load_config()
    profile = _profile(args, config)
    return Client(profile=profile, api_url=getattr(args, "api_url", None))


def _login(args) -> int:
    config = load_config()
    profile = _profile(args, config)
    if args.token_stdin:
        token = sys.stdin.read().strip()
        if not token:
            raise ValueError("stdin did not contain an API key")
    else:
        target = config.profile(profile)
        open_browser = not args.no_browser and can_open_browser()

        def started(code: str, url: str) -> None:
            print()
            print(f"  Approval code: {code}")
            print(f"  Approval URL:  {url}")
            print()
            if open_browser:
                print("If the browser did not open, paste the URL into any browser.")
            else:
                print(
                    "Open the URL in a browser on any device, sign in or create a CHEESE account,"
                )
                print("and approve the connection. The code above must match what the page shows.")
            print("Waiting for approval… (Ctrl+C to cancel)", flush=True)

        if open_browser:
            print(f"Opening {target.web_url} to approve this device…")
        elif args.no_browser:
            print(f"Starting login for {target.web_url} without opening a browser…")
        else:
            print(f"No display detected; starting login for {target.web_url} without a browser…")
        token, _, _ = browser_login(
            target.web_url,
            application="deepmedchem-python",
            open_browser=open_browser,
            timeout=args.timeout,
            on_started=started,
        )
    store = save_api_key(token, profile=profile)
    if store == FILE_STORE:
        print(
            f"Authenticated (profile: {profile}). No OS keyring is available here, so the "
            f"credential was saved to {credentials_path()} (mode 0600)."
        )
    else:
        print(f"Authenticated (profile: {profile}). Credential saved in the OS credential store.")
    return _verify_saved_key(profile)


def _verify_saved_key(profile: str) -> int:
    """Confirm the stored key is accepted by the API so a bad key surfaces at login time."""

    try:
        with Client(profile=profile) as client:
            client.catalog()
    except DeepMedChemError as error:
        if error.status_code == 401:
            print(
                "warning: the API rejected the approved key (it may be expired or revoked). "
                "Run `dmc login` again and choose 'Create a new key' or enable auto-renew "
                "on the approval page.",
                file=sys.stderr,
            )
            return 1
        print(f"warning: could not verify the key yet: {error}", file=sys.stderr)
    return 0


def _status(args) -> int:
    config = load_config()
    profile = _profile(args, config)
    target = config.profile(profile)
    authenticated = bool(get_stored_api_key(profile=profile))
    payload = {
        "profile": profile,
        "api_url": target.api_url,
        "web_url": target.web_url,
        "account_url": target.account_url,
        "config_path": str(config_path()),
        "authenticated": authenticated,
    }
    if args.verify:
        if not authenticated:
            payload["verified"] = False
            payload["error"] = "no stored credential"
        else:
            try:
                with Client(profile=profile) as client:
                    client.catalog()
                    usage = client.usage()
                payload["verified"] = True
                payload["plan"] = usage.plan
                payload["credits_remaining"] = "unlimited" if usage.unlimited else usage.remaining
                payload["credits_limit"] = usage.limit
            except DeepMedChemError as error:
                payload["verified"] = False
                payload["error"] = str(error)
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    return 0 if payload.get("verified", True) else 1


def _logout(args) -> int:
    config = load_config()
    if args.all:
        delete_all_api_keys(config)
        print("All local DeepMedChem credentials removed.")
    else:
        profile = _profile(args, config)
        delete_api_key(profile=profile)
        print(f"Local credential removed (profile: {profile}).")
    return 0


def _usage(args) -> int:
    with _open_client(args) as client:
        usage = client.usage()
    if args.json:
        print(json.dumps(usage.raw, indent=2, sort_keys=True))
    else:
        _print_usage(usage)
    return 0


def _databases(args) -> int:
    with _open_client(args) as client:
        catalog = client.catalog()
    if args.json:
        print(json.dumps(catalog, indent=2, sort_keys=True))
    else:
        _print_database_table(catalog)
    return 0


def _emit_result(args, result: SearchResult, client: Client | None = None) -> int:
    library = None
    if not args.json and client is not None:
        library = _catalog_entry(client, result.meta.database)
    if args.output:
        selected = args.format or infer_format(args.output)
        written = write_result(result, args.output, format=selected)
        if not args.json:
            _print_result_table(result, library=library)
            print(f"Saved {written} molecules to {args.output} ({selected}).")
        else:
            print(json.dumps(result.raw, indent=2, sort_keys=True))
            print(f"Saved {written} molecules to {args.output} ({selected}).", file=sys.stderr)
        return 0
    if args.json:
        print(json.dumps(result.raw, indent=2, sort_keys=True))
    else:
        _print_result_table(result, library=library)
    return 0


def _search(args) -> int:
    with _open_client(args) as client:
        result = client.search(
            args.smiles,
            database=args.database,
            method=args.method,
            limit=args.limit,
            shortlist_multiplier=args.shortlist_multiplier,
            include_synthons=args.include_synthons,
        )
        return _emit_result(args, result, client)


def _substructure(args) -> int:
    with _open_client(args) as client:
        result = client.search_substructure(
            args.query,
            query_format=args.query_format,
            database=args.database,
            limit=args.limit,
            timeout_seconds=args.timeout_seconds,
            include_synthons=args.include_synthons,
        )
        return _emit_result(args, result, client)


def _sample(args) -> int:
    with _open_client(args) as client:
        result = client.sample(
            database=args.database,
            count=args.count,
            seed=args.seed,
            include_synthons=args.include_synthons,
        )
        return _emit_result(args, result, client)


def _order(args) -> int:
    bundle = prepare_order(
        args.input,
        get_quote=args.get_quote,
        database=args.database,
        output_dir=args.output_dir,
        to=args.to,
        cc=args.cc,
        amount_mg=args.amount_mg,
        name=args.name,
    )
    print(
        f"Prepared {len(bundle.drafts)} vendor request(s) for {bundle.molecule_count} molecule(s) "
        f"in {bundle.directory}."
    )
    for draft in bundle.drafts:
        print(f"  {draft.vendor}: {len(draft.molecules)} molecules -> {draft.email}")

    if args.no_open:
        print("Email drafts were not opened (--no-open). Use each vendor's email.txt.")
        return 0
    if not can_open_browser():
        print("No graphical mail client detected. Use each vendor's email.txt and molecules.csv.")
        return 0
    opened = open_order_drafts(bundle)
    if opened == len(bundle.drafts):
        print(f"Requested opening {opened} email draft(s). Review them before sending.")
    else:
        print(
            f"Requested opening {opened} of {len(bundle.drafts)} email draft(s). "
            "Use email.txt for any draft that did not open."
        )
    return 0


_COMMANDS = {
    "login": _login,
    "status": _status,
    "logout": _logout,
    "usage": _usage,
    "databases": _databases,
    "catalog": _databases,
    "search": _search,
    "substructure": _substructure,
    "sample": _sample,
    "order": _order,
}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except (CredentialError, LoginError, ValueError, ImportError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except DeepMedChemError as error:
        suffix = f" [{error.code}]" if error.code else ""
        print(f"error: {error}{suffix}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
