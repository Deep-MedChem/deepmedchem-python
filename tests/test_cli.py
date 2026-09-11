import argparse
import functools
import io
import re
from pathlib import Path

import httpx

import deepmedchem.cli as cli
from deepmedchem import Client


def _mock_client(monkeypatch, status_code: int):
    def handler(request):
        if status_code == 200:
            return httpx.Response(200, json={"libraries": []})
        return httpx.Response(
            status_code, json={"error": {"code": "unauthorized", "message": "A valid API key."}}
        )

    monkeypatch.setattr(
        cli,
        "Client",
        functools.partial(
            Client,
            api_key="secret-value",
            api_url="https://api.example.test",
            transport=httpx.MockTransport(handler),
            max_retries=0,
        ),
    )


def test_token_is_never_a_command_line_option() -> None:
    parser = cli._parser()
    login = next(action for action in parser._actions if action.dest == "command").choices["login"]
    options = {option for action in login._actions for option in action.option_strings}
    assert "--token-stdin" in options
    assert "--token" not in options


def test_login_from_stdin_saves_only_to_selected_profile(monkeypatch, capsys) -> None:
    saved = []
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("secret-value\n"))
    monkeypatch.setattr(cli, "save_api_key", lambda token, profile: saved.append((token, profile)))
    _mock_client(monkeypatch, 200)
    assert cli.main(["login", "--profile", "dev", "--token-stdin"]) == 0
    assert saved == [("secret-value", "dev")]
    assert "secret-value" not in capsys.readouterr().out


def test_login_warns_when_the_api_rejects_the_saved_key(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("secret-value\n"))
    monkeypatch.setattr(cli, "save_api_key", lambda token, profile: cli.FILE_STORE)
    _mock_client(monkeypatch, 401)
    assert cli.main(["login", "--token-stdin"]) == 1
    captured = capsys.readouterr()
    assert "Authenticated" in captured.out
    assert "rejected the approved key" in captured.err
    assert "secret-value" not in captured.out + captured.err


def test_headless_login_prints_url_and_reports_file_store(monkeypatch, capsys) -> None:
    calls = {}

    def fake_browser_login(web_url, **kwargs):
        calls["open_browser"] = kwargs["open_browser"]
        kwargs["on_started"]("ABCD-2345", f"{web_url}/navigator/login?code=ABCD-2345")
        return "secret-value", "ABCD-2345", "url"

    monkeypatch.setattr(cli, "browser_login", fake_browser_login)
    monkeypatch.setattr(cli, "can_open_browser", lambda: False)
    monkeypatch.setattr(cli, "save_api_key", lambda token, profile: cli.FILE_STORE)
    _mock_client(monkeypatch, 200)
    assert cli.main(["login"]) == 0
    out = capsys.readouterr().out
    assert calls["open_browser"] is False
    assert "ABCD-2345" in out
    assert "/navigator/login?code=ABCD-2345" in out
    assert "No display detected" in out
    assert "credentials.json" in out
    assert "secret-value" not in out


def test_the_skill_cli_reference_documents_every_public_flag() -> None:
    """`skills/.../cli.md` promises "every command and flag"; hold it to that.

    Only flags argparse actually advertises count. `--api-url`, `--cc` and
    `--include-synthons` carry help=SUPPRESS, so they are deliberately absent from
    `--help` and belong out of the reference too.
    """

    reference = (
        Path(__file__).parents[1] / "skills" / "deepmedchem" / "references" / "cli.md"
    ).read_text(encoding="utf-8")
    parser = cli._parser()
    commands = next(a for a in parser._actions if getattr(a, "choices", None))

    def documented(option: str) -> bool:
        # A bare substring test would let `--token-stdin` vouch for `--to`.
        return re.search(re.escape(option) + r"(?![\w-])", reference) is not None

    undocumented = sorted(
        {
            option
            for source in [parser, *commands.choices.values()]
            for action in source._actions
            if action.help is not argparse.SUPPRESS
            for option in action.option_strings
            if option.startswith("--") and option != "--help" and not documented(option)
        }
    )
    assert undocumented == []
