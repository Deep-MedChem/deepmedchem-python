"""Standard-library CLI for DeepMedChem: authentication, catalog, searches, usage, optimization."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path
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
from .databases import DATABASE_DETAILS, DATABASE_DISPLAY_ORDER
from .export import FORMATS, infer_format, write_result
from .models import Batch, OptimizationResource, OptimizationResult, SearchResult, Usage
from .optimization import _drive, _filename, normalize_scores
from .ordering import open_order_drafts, prepare_order, procurement_contacts

SEARCH_METHODS = ("morgan", "shape", "esp")


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


def _library_count(library: dict[str, Any]) -> str:
    size = _human_count(library.get("product_count"))
    if size != "-" and (library.get("population") or {}).get("count_is_estimate"):
        return f"~{size}"
    return size


def _print_database_table(catalog: dict[str, Any], *, detailed: bool = False) -> None:
    priority = {database_id: index for index, database_id in enumerate(DATABASE_DISPLAY_ORDER)}
    libraries = sorted(
        catalog.get("libraries") or [],
        key=lambda library: priority.get(str(library.get("database_id") or ""), len(priority)),
    )
    contacts = procurement_contacts()
    rows = []
    for library in libraries:
        database_id = str(library.get("database_id") or "")
        contact = contacts.get(database_id.casefold())
        pricing = library.get("pricing") or {}
        details = DATABASE_DETAILS.get(database_id, {})
        row = [
            details.get("abbreviation", database_id),
            _library_count(library),
            "yes" if pricing.get("available") else "-",
            contact.email if contact else "-",
        ]
        if detailed:
            row = [
                row[0],
                database_id,
                details.get("biosolveit", "-"),
                details.get("type", "-"),
                row[1],
                details.get("availability", "-"),
                details.get("success", "-"),
                row[2],
                row[3],
                details.get("url", "-"),
            ]
        rows.append(row)
    headers = ["abbreviation", "molecules", "prices", "orders"]
    if detailed:
        headers = [
            "abbreviation",
            "database_id",
            "biosolveit_mapping",
            "type",
            "molecules",
            "availability",
            "success",
            "prices",
            "orders",
            "link",
        ]
    print(_format_table(headers, rows, align_right=[h == "molecules" for h in headers]))
    print()
    print(f"{len(rows)} databases. Lead times vary by database; see --detailed.")
    print("Order or request quotes by email, or run `dmc order results.csv`.")
    if detailed:
        print("Availability and success: provider estimates; releases may differ.")
        if any(row[1] == "vast-2026-h2" for row in rows):
            print("VAST H2 2026: average lead time 2-4 weeks; synthesis success 85%+ (XtalPi).")
        print("* No direct mapping: CHEMriya is a related Otava collection.")
        print("** No direct mapping: our Enamine version uses public Enamine building blocks.")
        print(
            "Only live API catalog entries are listed; other CHEESE UI databases are coming soon."
        )


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
    size = _library_count(library) if library else "-"
    unit = "source combinations" if (library or {}).get("population") else "molecules"
    space = f"{size} {unit} ({name})" if size != "-" else str(name)
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
    databases.add_argument(
        "--detailed", action="store_true", help="Show full IDs, mappings and provider details"
    )

    search = commands.add_parser("search", help="Similarity search for a SMILES query")
    search.add_argument("smiles", help="Query molecule as SMILES")
    search.add_argument(
        "-d", "--database", required=True, help="Database abbreviation or full ID, see `databases`"
    )
    search.add_argument(
        "-m", "--method", choices=SEARCH_METHODS, default="morgan", help="Similarity method"
    )
    search.add_argument("-n", "--limit", type=int, default=20, help="Number of hits")
    _add_output_options(search)
    _add_connection_options(search)

    substructure = commands.add_parser("substructure", help="Exact SMILES/SMARTS substructure")
    substructure.add_argument("query", help="Substructure query")
    substructure.add_argument(
        "-d", "--database", required=True, help="Database abbreviation or full ID"
    )
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
    sample.add_argument("-d", "--database", required=True, help="Database abbreviation or full ID")
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
    _add_optimize_parser(commands)
    return parser


def _add_optimize_parser(commands) -> None:
    optimize = commands.add_parser(
        "optimize",
        help="Optimize your own score over a chemical space with Navigator (ask/tell)",
        description=(
            "Navigator proposes molecules, your scorer scores them, the scores go back. "
            "`run` drives the whole loop with a shell command; `ask` and `tell` split it for "
            "HPC queues."
        ),
    )
    actions = optimize.add_subparsers(dest="optimize_command", required=True, metavar="COMMAND")

    run = actions.add_parser(
        "run",
        help="Run the loop with a scoring command until the budget is spent",
        description=(
            "Create (or resume) the optimization NAME and score every batch with --score-cmd. "
            "The command receives {input}, a CSV with id,smiles, and must write {output}, a CSV "
            "with id,score and optionally status, error and metric columns. Re-running the same "
            "command resumes."
        ),
    )
    run.add_argument("name", metavar="NAME", help="Optimization name; re-running it resumes")
    run.add_argument("-d", "--database", required=True, help="Database abbreviation or full ID")
    direction = run.add_mutually_exclusive_group(required=True)
    direction.add_argument(
        "--minimize", dest="direction", action="store_const", const="minimize",
        help="Lower scores are better (docking)",
    )
    direction.add_argument(
        "--maximize", dest="direction", action="store_const", const="maximize",
        help="Higher scores are better (predicted activity)",
    )
    run.add_argument("--budget", type=int, default=1000, help="Molecules to score in total")
    run.add_argument("--batch-size", type=int, default=100, help="Molecules per batch")
    run.add_argument(
        "--score-cmd",
        required=True,
        metavar="CMD",
        help="Shell command with {input} and {output} placeholders, "
        "e.g. './dock.sh {input} {output}'",
    )
    run.add_argument("--strategy", help="Navigator strategy (default: gamma_diversity_screening)")
    run.add_argument("--druglike", action="store_true", help="Apply the druglike filter")
    run.add_argument(
        "--property",
        action="append",
        default=[],
        metavar="NAME=MIN:MAX",
        help="Descriptor range, e.g. MolWt=:500 or TPSA=20:140 (repeatable)",
    )
    run.add_argument("--seed", type=int, help="Engine seed (default 0)")
    run.add_argument(
        "--scorer", metavar="JSON", help='Scorer identity to pin, e.g. \'{"name": "glide"}\''
    )
    run.add_argument("--hit-threshold", type=float, help="Score threshold defining a hit")
    run.add_argument("--objective-name", help="Label of the score, e.g. docking_score")
    run.add_argument("--units", help="Units of the score, e.g. kcal/mol")
    run.add_argument(
        "--work-dir",
        metavar="DIR",
        help="Keep each batch's input and output CSV here (default: a temporary directory)",
    )
    run.add_argument("--quiet", action="store_true", help="Do not print per-round progress")
    _add_connection_options(run)

    transition = actions.add_parser("transition", help="Switch strategy after the pending batch")
    transition.add_argument("name")
    transition.add_argument("strategy")
    transition.add_argument("--idempotency-key", required=True)
    _add_connection_options(transition)
    seeds = actions.add_parser("seeds", help="Queue measured seeds from a JSON list")
    seeds.add_argument("name")
    seeds.add_argument("input")
    seeds.add_argument("--mode", choices=("external", "synthon"), required=True)
    seeds.add_argument("--scorer", metavar="JSON", help="Same scorer identity as the run")
    seeds.add_argument("--idempotency-key", required=True)
    _add_connection_options(seeds)

    ask = actions.add_parser(
        "ask",
        help="Write the pending batch to a CSV (id,smiles)",
        description=(
            "Exit codes: 0 batch written; 3 no batch ready within --wait; 4 the optimization "
            "has finished."
        ),
    )
    ask.add_argument("name", metavar="NAME", help="Optimization name or id")
    ask.add_argument("-o", "--output", metavar="FILE", help="CSV to write (default: BATCH_ID.csv)")
    ask.add_argument(
        "--wait", type=float, default=300.0, metavar="SECONDS",
        help="How long to wait for a batch (default: 300)",
    )
    _add_connection_options(ask)

    tell = actions.add_parser("tell", help="Submit a scores CSV (id,score[,status,error,...])")
    tell.add_argument("name", metavar="NAME", help="Optimization name or id")
    tell.add_argument("scores", metavar="SCORES.csv", help="CSV with id and score columns")
    tell.add_argument(
        "--batch", metavar="BATCH_ID", help="Batch the scores belong to (default: the pending one)"
    )
    tell.add_argument("--scorer", metavar="JSON", help="Scorer identity, checked against the pin")
    _add_connection_options(tell)

    status = actions.add_parser("status", help="Show one optimization, or list them all")
    status.add_argument("name", metavar="NAME", nargs="?", help="Optimization name or id")
    _add_connection_options(status)

    listing = actions.add_parser("list", help="List your optimizations")
    listing.add_argument("--status", dest="status_filter", help="Only this status")
    _add_connection_options(listing)

    results = actions.add_parser("results", help="Show or export the scored molecules")
    results.add_argument("name", metavar="NAME", help="Optimization name or id")
    results.add_argument("--top", type=int, metavar="N", help="Only the N best molecules")
    results.add_argument(
        "--order", choices=("best", "round"), default="best", help="Sort order (default: best)"
    )
    results.add_argument("-o", "--output", metavar="FILE", help="Write the rows to a CSV file")
    _add_connection_options(results)

    for action in ("cancel", "resume"):
        command = actions.add_parser(
            action,
            help="Cancel an optimization" if action == "cancel" else "Resume a paused optimization",
        )
        command.add_argument("name", metavar="NAME", help="Optimization name or id")
        _add_connection_options(command)


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
        _print_database_table(catalog, detailed=args.detailed)
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


# --- Optimization commands ------------------------------------------------------------

OPTIMIZE_NOT_READY = 3
OPTIMIZE_FINISHED = 4
_MISSING_SCORE = {"", "nan", "none", "null", "na", "n/a"}
_INTEGER = re.compile(r"^[+-]?\d+$")


class ScoreCommandError(RuntimeError):
    """The scoring command failed; nothing was submitted."""


def _json_option(value: str | None, flag: str) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except ValueError as error:
        raise ValueError(f"{flag} must be a JSON object: {error}") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{flag} must be a JSON object")
    return parsed


def _property_options(values: Sequence[str]) -> dict[str, dict[str, float]] | None:
    properties: dict[str, dict[str, float]] = {}
    for value in values:
        name, separator, bounds = value.partition("=")
        low, colon, high = bounds.partition(":")
        if not separator or not colon or not name.strip():
            raise ValueError(
                f"--property must look like NAME=MIN:MAX (either bound may be empty): {value}"
            )
        entry = {}
        for key, text in (("min", low.strip()), ("max", high.strip())):
            if text:
                try:
                    entry[key] = float(text)
                except ValueError as error:
                    raise ValueError(f"--property {value}: {text!r} is not a number") from error
        properties[name.strip()] = entry
    return properties or None


def _csv_value(text: str) -> Any:
    if _INTEGER.match(text):
        return int(text)
    try:
        return float(text)
    except ValueError:
        return text


def read_scores_csv(path: str | Path) -> dict[str, dict[str, Any]]:
    """Read ``id,score[,status,error,metric...]`` rows into a mapping for normalize_scores()."""

    rows: dict[str, dict[str, Any]] = {}
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = [str(name).strip() for name in reader.fieldnames or []]
        if "id" not in columns or "score" not in columns:
            raise ValueError(
                f"{path} must have 'id' and 'score' columns; found {columns or 'no header'}"
            )
        for line, record in enumerate(reader, start=2):
            cells = {
                str(key).strip(): (value or "").strip()
                for key, value in record.items()
                if key is not None and isinstance(value, (str, type(None)))
            }
            molecule_id = cells.pop("id", "")
            if not molecule_id:
                raise ValueError(f"{path}:{line}: empty id")
            if molecule_id in rows:
                raise ValueError(f"{path}:{line}: duplicate id {molecule_id!r}")
            raw = cells.pop("score", "")
            entry: dict[str, Any] = {"score": None}
            if raw.lower() not in _MISSING_SCORE:
                try:
                    entry["score"] = float(raw)
                except ValueError:
                    entry["error"] = f"unparseable score {raw[:50]!r}"
            status = cells.pop("status", "")
            if status:
                entry["status"] = status
            error = cells.pop("error", "")
            if error:
                entry["error"] = error
            cells.pop("smiles", None)
            for key, value in cells.items():
                if key and value:
                    entry[key] = _csv_value(value)
            rows[molecule_id] = entry
    return rows


def _substitute(template: str, input_path: Path, output_path: Path) -> str:
    return template.replace("{input}", shlex.quote(str(input_path))).replace(
        "{output}", shlex.quote(str(output_path))
    )


def _score_with_command(
    template: str, batch: Batch, *, work_dir: str | None, quiet: bool
) -> list[dict[str, Any]]:
    directory = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="dmc-optimize-"))
    directory.mkdir(parents=True, exist_ok=True)
    stem = _filename(batch.id)
    input_path = directory / f"{stem}.input.csv"
    output_path = directory / f"{stem}.scores.csv"
    batch.to_csv(input_path)
    if output_path.exists():
        output_path.unlink()
    command = _substitute(template, input_path, output_path)
    if not quiet:
        print(f"Scoring batch {batch.id} ({len(batch)} molecules): {command}", file=sys.stderr)
    finished = subprocess.run(command, shell=True, check=False)
    if finished.returncode != 0:
        raise ScoreCommandError(
            f"The score command exited with status {finished.returncode}; nothing was "
            f"submitted and batch {batch.id} stays pending. Its input is {input_path}."
        )
    if not output_path.is_file():
        raise ScoreCommandError(
            f"The score command did not write {output_path}; nothing was submitted and batch "
            f"{batch.id} stays pending."
        )
    rows = normalize_scores(batch, read_scores_csv(output_path))
    if not work_dir:
        shutil.rmtree(directory, ignore_errors=True)
    return rows


def _status_line(resource: OptimizationResource) -> str:
    reason = f" ({resource.status_reason})" if resource.status_reason else ""
    return f"{resource.status}{reason}"


def _print_optimization(resource: OptimizationResource) -> None:
    spec = resource.specification
    objective = spec.get("objective") or {}
    progress = resource.progress
    best = resource.best
    print(f"name:       {resource.name or '-'}")
    print(f"id:         {resource.id}")
    print(f"status:     {_status_line(resource)}")
    print(f"database:   {spec.get('database', '-')}")
    print(f"objective:  {objective.get('direction', '-')} {objective.get('name') or ''}".rstrip())
    print(f"strategy:   {spec.get('strategy') or (resource.engine or {}).get('strategy') or '-'}")
    print(f"round:      {resource.round}")
    print(
        f"scored:     {progress.scored}/{progress.budget} "
        f"({progress.valid} valid, {progress.failed} failed)"
    )
    if resource.pending_batch_id:
        print(f"pending:    {resource.pending_batch_id}")
    if best is not None:
        print(f"best:       {_score(best.score)}  {best.smiles or ''}  ({best.id})")


def _print_optimization_table(resources: Sequence[OptimizationResource]) -> None:
    rows = [
        [
            resource.name or "-",
            resource.id,
            _status_line(resource),
            resource.round,
            f"{resource.progress.scored}/{resource.progress.budget}",
            _score(resource.best.score) if resource.best is not None else "-",
        ]
        for resource in resources
    ]
    headers = ["name", "id", "status", "round", "scored", "best"]
    print(_format_table(headers, rows, align_right=[False, False, False, True, True, True]))
    print()
    print(f"{len(rows)} optimizations.")


def _print_observations(result: OptimizationResult, *, shown: int) -> None:
    rows = [
        [index, _score(row.score), row.status, row.round, row.id, row.smiles or ""]
        for index, row in enumerate(list(result)[:shown], start=1)
    ]
    headers = ["rank", "score", "status", "round", "id", "smiles"]
    print(_format_table(headers, rows, align_right=[True, True, False, True, False, False]))


def _optimize_run(args) -> int:
    if "{input}" not in args.score_cmd or "{output}" not in args.score_cmd:
        raise ValueError("--score-cmd must contain both {input} and {output}")
    scorer = _json_option(args.scorer, "--scorer")
    with _open_client(args) as client:
        optimization = client.optimizations.create(
            database=args.database,
            direction=args.direction,
            budget=args.budget,
            batch_size=args.batch_size,
            name=args.name,
            strategy=args.strategy,
            filters="druglike" if args.druglike else None,
            properties=_property_options(args.property),
            seed=args.seed,
            scorer=scorer,
            objective_name=args.objective_name,
            hit_threshold=args.hit_threshold,
            units=args.units,
        )
        try:
            result = _drive(
                optimization,
                lambda batch: _score_with_command(
                    args.score_cmd, batch, work_dir=args.work_dir, quiet=args.quiet
                ),
                scorer=scorer,
                progress=not args.quiet,
                named=True,
                resume_hint="re-run the same `dmc optimize run` command to resume",
            )
        except KeyboardInterrupt:
            return 130
    if args.json:
        print(json.dumps(result.optimization.raw if result.optimization else {}, indent=2))
    else:
        top = result.top(10)
        if top:
            _print_observations(OptimizationResult(observations=top), shown=10)
        print(f"Export everything with `dmc optimize results {args.name} -o results.csv`.")
    return 0


def _optimize_ask(args) -> int:
    with _open_client(args) as client:
        optimization = client.optimizations.get(args.name)
        try:
            batch = optimization.ask(timeout=max(0.0, args.wait))
        except DeepMedChemError as error:
            if error.code != "client_timeout":
                raise
            print(
                f"No batch is ready yet (status: {optimization.status}); ask again later.",
                file=sys.stderr,
            )
            return OPTIMIZE_NOT_READY
    if batch is None:
        print(
            f"Optimization {args.name} is {_status_line(optimization.resource)}; there is "
            "nothing left to score.",
            file=sys.stderr,
        )
        return OPTIMIZE_FINISHED
    path = args.output or f"{_filename(batch.id)}.csv"
    batch.to_csv(path)
    if args.json:
        payload = {
            "optimization_id": optimization.id,
            "batch_id": batch.id,
            "round": batch.round,
            "molecules": len(batch),
            "path": str(path),
        }
        print(json.dumps(payload, indent=2))
    else:
        print(f"Wrote batch {batch.id} (round {batch.round}, {len(batch)} molecules) to {path}.")
        print(f"Score it, then run: dmc optimize tell {args.name} SCORES.csv")
    return 0


def _optimize_tell(args) -> int:
    scores = read_scores_csv(args.scores)
    scorer = _json_option(args.scorer, "--scorer")
    with _open_client(args) as client:
        optimization = client.optimizations.get(args.name)
        resource, pending = client.optimizations.next_batch(optimization.id, wait=0)
        batch_id = args.batch or (pending.id if pending is not None else None)
        if batch_id is None:
            raise ValueError(
                f"Optimization {args.name} has no pending batch "
                f"(status: {_status_line(resource)}). Pass --batch BATCH_ID to re-send the "
                "scores of an earlier batch."
            )
        if pending is None or pending.id != batch_id:
            # An earlier batch: normalize against it so a re-send rebuilds the same rows.
            pending = client.optimizations.batch(optimization.id, batch_id)
        rows = normalize_scores(pending, scores)
        receipt = client.optimizations.submit(optimization.id, batch_id, rows, scorer=scorer)
    if args.json:
        print(json.dumps(receipt.raw, indent=2))
        return 0
    counts = ", ".join(f"{count} {status}" for status, count in sorted(receipt.counts.items()))
    if receipt.duplicate:
        print(f"Batch {batch_id} was already accepted with these scores; nothing changed.")
    else:
        print(f"Submitted {len(rows)} scores for batch {batch_id} ({counts}).")
    if receipt.optimization is not None:
        progress = receipt.optimization.progress
        print(f"{progress.scored}/{progress.budget} molecules scored.")
    return 0


def _optimize_status(args) -> int:
    with _open_client(args) as client:
        if getattr(args, "name", None):
            resource = client.optimizations.get(args.name).resource
            if args.json:
                print(json.dumps(resource.raw, indent=2, sort_keys=True))
            else:
                _print_optimization(resource)
            return 0
        resources = client.optimizations.list(status=getattr(args, "status_filter", None))
    if args.json:
        print(json.dumps([resource.raw for resource in resources], indent=2, sort_keys=True))
    else:
        _print_optimization_table(resources)
    return 0


def _optimize_results(args) -> int:
    if args.top is not None and args.top < 1:
        raise ValueError("--top must be a positive integer")
    with _open_client(args) as client:
        result = client.optimizations.get(args.name).results(order=args.order, limit=args.top)
    if args.output:
        written = result.to_csv(args.output)
    if args.json:
        print(json.dumps(result.to_records(), indent=2))
    else:
        shown = min(len(result), args.top or 20)
        _print_observations(result, shown=shown)
        if len(result) > shown:
            print(f"... {len(result) - shown} more; use --top N or -o FILE.")
    if args.output:
        print(f"Saved {written} rows to {args.output}.", file=sys.stderr if args.json else None)
    return 0


def _optimize_cancel_or_resume(args) -> int:
    with _open_client(args) as client:
        optimization = client.optimizations.get(args.name)
        if args.optimize_command == "cancel":
            optimization.cancel()
        else:
            optimization.resume()
    if args.json:
        print(json.dumps(optimization.resource.raw, indent=2, sort_keys=True))
    else:
        print(f"{optimization.name or optimization.id}: {_status_line(optimization.resource)}")
    return 0


def _optimize_control(args) -> int:
    with _open_client(args) as client:
        optimization = client.optimizations.get(args.name)
        if args.optimize_command == "transition":
            optimization.transition(args.strategy, idempotency_key=args.idempotency_key)
        else:
            rows = json.loads(Path(args.input).read_text())
            optimization.add_seeds(
                rows, mode=args.mode, scorer=_json_option(args.scorer, "--scorer"),
                idempotency_key=args.idempotency_key)
    print(json.dumps(optimization.resource.raw, indent=2, sort_keys=True))
    return 0


_OPTIMIZE_COMMANDS = {
    "run": _optimize_run,
    "ask": _optimize_ask,
    "tell": _optimize_tell,
    "status": _optimize_status,
    "list": _optimize_status,
    "results": _optimize_results,
    "cancel": _optimize_cancel_or_resume,
    "resume": _optimize_cancel_or_resume,
    "seeds": _optimize_control,
    "transition": _optimize_control,
}


def _optimize(args) -> int:
    return _OPTIMIZE_COMMANDS[args.optimize_command](args)


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
    "optimize": _optimize,
}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except (CredentialError, LoginError, ValueError, ImportError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except ScoreCommandError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except DeepMedChemError as error:
        suffix = f" [{error.code}]" if error.code else ""
        print(f"error: {error}{suffix}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
