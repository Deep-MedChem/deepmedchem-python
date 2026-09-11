"""Keep the suite off the developer's own credentials.

Two stores are involved. `config.credentials_path()` is derived from
`config.config_path()`, so it resolves to the user's configuration directory unless that
is redirected. The OS keyring is reached through `config._keyring()`, which
`delete_api_key` calls whatever the store preference says, so an env var cannot fence it
off. A test that exercises credential storage without both redirected reads, rewrites and
deletes the credentials the developer actually uses.
"""

import pytest

import deepmedchem.cli as cli
import deepmedchem.config as config

ENVIRONMENT = (
    "DEEPMEDCHEM_API_KEY",
    "DMC_API_KEY",
    "CHEESE_API_KEY",
    "DEEPMEDCHEM_PROFILE",
    "DEEPMEDCHEM_CREDENTIAL_STORE",
)


class _EmptyKeyring:
    """Stands in for the keyring module: reachable, and holding nothing.

    `errors` mirrors the real hierarchy rather than listing the classes this branch
    happens to catch. Every class keyring defines descends from `KeyringError`, and
    `NoKeyringError` also from `RuntimeError`; flattening that lets a fake satisfy an
    isinstance check the installed backends would fail.
    """

    class errors:
        class KeyringError(Exception):
            pass

        class PasswordDeleteError(KeyringError):
            pass

        class PasswordSetError(KeyringError):
            pass

        class NoKeyringError(KeyringError, RuntimeError):
            pass

        class KeyringLocked(KeyringError):
            pass

    def get_password(self, service, account):
        return None

    def set_password(self, service, account, value):
        return None

    def delete_password(self, service, account):
        raise self.errors.PasswordDeleteError(account)


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(config, "_legacy_config_path", lambda: tmp_path / "config.json")
    # cli imported config_path by value, so patching config alone leaves it pointing at
    # the real file. credentials_path needs no equivalent: its body resolves config_path
    # from config's globals when called.
    monkeypatch.setattr(cli, "config_path", lambda: tmp_path / "config.toml")
    # Tests that care about keyring behaviour replace this themselves; the default only
    # has to be somewhere other than the developer's real backend.
    monkeypatch.setattr(config, "_keyring", _EmptyKeyring)
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    return tmp_path
