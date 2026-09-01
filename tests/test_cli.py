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
