"""Small standard-library CLI for DeepMedChem authentication."""

from __future__ import annotations

import argparse
import json
import sys

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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deepmedchem")
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
    return parser


def _profile(args, config) -> str:
    selected = resolve_profile(args.profile, config)
    config.profile(selected)
    return selected


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
                payload["verified"] = True
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "login":
            return _login(args)
        if args.command == "status":
            return _status(args)
        return _logout(args)
    except (CredentialError, LoginError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
