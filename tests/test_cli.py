import io

import deepmedchem.cli as cli


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
    assert cli.main(["login", "--profile", "dev", "--token-stdin"]) == 0
    assert saved == [("secret-value", "dev")]
    assert "secret-value" not in capsys.readouterr().out


def test_headless_login_prints_url_and_reports_file_store(monkeypatch, capsys) -> None:
    calls = {}

    def fake_browser_login(web_url, **kwargs):
        calls["open_browser"] = kwargs["open_browser"]
        kwargs["on_started"]("ABCD-2345", f"{web_url}/navigator/login?code=ABCD-2345")
        return "secret-value", "ABCD-2345", "url"

    monkeypatch.setattr(cli, "browser_login", fake_browser_login)
    monkeypatch.setattr(cli, "can_open_browser", lambda: False)
    monkeypatch.setattr(cli, "save_api_key", lambda token, profile: cli.FILE_STORE)
    assert cli.main(["login"]) == 0
    out = capsys.readouterr().out
    assert calls["open_browser"] is False
    assert "ABCD-2345" in out
    assert "/navigator/login?code=ABCD-2345" in out
    assert "No display detected" in out
    assert "credentials.json" in out
    assert "secret-value" not in out
