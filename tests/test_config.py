import deepmedchem.config as config
from deepmedchem.config import resolve_api_key


class Provider:
    def get_api_key(self):
        return "provider-key"


def test_credential_precedence(monkeypatch) -> None:
    monkeypatch.setenv("DEEPMEDCHEM_API_KEY", "environment-key")
    assert resolve_api_key("explicit-key", Provider()) == "explicit-key"
    assert resolve_api_key(credential_provider=Provider()) == "environment-key"
    monkeypatch.delenv("DEEPMEDCHEM_API_KEY")
    monkeypatch.delenv("DMC_API_KEY", raising=False)
    assert resolve_api_key(credential_provider=Provider()) == "provider-key"


def test_legacy_dmc_api_key_is_supported(monkeypatch) -> None:
    monkeypatch.delenv("DEEPMEDCHEM_API_KEY", raising=False)
    monkeypatch.setenv("DMC_API_KEY", "legacy-environment-key")
    assert resolve_api_key() == "legacy-environment-key"


def test_existing_cheese_api_key_environment_is_supported(monkeypatch) -> None:
    monkeypatch.delenv("DEEPMEDCHEM_API_KEY", raising=False)
    monkeypatch.delenv("DMC_API_KEY", raising=False)
    monkeypatch.setenv("CHEESE_API_KEY", "existing-cheese-key")
    assert resolve_api_key() == "existing-cheese-key"


def test_legacy_navigator_keyring_entry_migrates(monkeypatch) -> None:
    values = {(config.LEGACY_SERVICE, config.LEGACY_ACCOUNT): "navigator-key"}

    class FakeKeyring:
        def get_password(self, service, account):
            return values.get((service, account))

        def set_password(self, service, account, value):
            values[(service, account)] = value

    monkeypatch.setattr(config, "_keyring", lambda: FakeKeyring())
    assert config.get_stored_api_key() == "navigator-key"
    assert values[(config.SERVICE, config.profile_account("default"))] == "navigator-key"
    assert config.migrate_legacy_api_key() is False


def test_profile_credentials_are_isolated(monkeypatch) -> None:
    values = {}

    class Errors:
        class PasswordDeleteError(Exception):
            pass

    class FakeKeyring:
        errors = Errors

        def get_password(self, service, account):
            return values.get((service, account))

        def set_password(self, service, account, value):
            values[(service, account)] = value

        def delete_password(self, service, account):
            if (service, account) not in values:
                raise self.errors.PasswordDeleteError()
            del values[(service, account)]

    monkeypatch.setattr(config, "_keyring", lambda: FakeKeyring())
    config.save_api_key("prod", profile="default")
    config.save_api_key("development", profile="dev")
    assert config.get_stored_api_key(profile="default") == "prod"
    assert config.get_stored_api_key(profile="dev") == "development"
    config.delete_api_key(profile="dev")
    assert config.get_stored_api_key(profile="default") == "prod"
    assert config.get_stored_api_key(profile="dev") is None


def test_file_store_fallback_when_keyring_is_unusable(monkeypatch, tmp_path) -> None:
    class NoBackend:
        def get_password(self, service, account):
            raise RuntimeError("No recommended backend was available")

        def set_password(self, service, account, value):
            raise RuntimeError("No recommended backend was available")

    monkeypatch.delenv("DEEPMEDCHEM_CREDENTIAL_STORE", raising=False)
    monkeypatch.setattr(config, "_keyring", lambda: NoBackend())
    monkeypatch.setattr(config, "config_path", lambda: tmp_path / "config.toml")

    assert config.get_stored_api_key(profile="default") is None
    assert config.save_api_key("headless-key", profile="default") == config.FILE_STORE
    path = config.credentials_path()
    assert path == tmp_path / "credentials.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert config.get_stored_api_key(profile="default") == "headless-key"
    assert config.get_stored_api_key(profile="dev") is None

    config.delete_api_key(profile="default")
    assert config.get_stored_api_key(profile="default") is None


def test_keyring_preferred_and_file_entry_cleared(monkeypatch, tmp_path) -> None:
    values = {}

    class FakeKeyring:
        def get_password(self, service, account):
            return values.get((service, account))

        def set_password(self, service, account, value):
            values[(service, account)] = value

    monkeypatch.delenv("DEEPMEDCHEM_CREDENTIAL_STORE", raising=False)
    monkeypatch.setattr(config, "_keyring", lambda: FakeKeyring())
    monkeypatch.setattr(config, "config_path", lambda: tmp_path / "config.toml")
    config._write_file_store({"default": "stale-file-key"})

    assert config.save_api_key("fresh-key") == config.KEYRING_STORE
    assert config.get_stored_api_key() == "fresh-key"
    assert config._read_file_store() == {}


def test_credential_store_can_be_forced(monkeypatch, tmp_path) -> None:
    class ExplodingKeyring:
        def set_password(self, *args):
            raise AssertionError("keyring must not be used")

        def get_password(self, *args):
            raise AssertionError("keyring must not be used")

    monkeypatch.setattr(config, "_keyring", lambda: ExplodingKeyring())
    monkeypatch.setattr(config, "config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setenv("DEEPMEDCHEM_CREDENTIAL_STORE", "file")
    assert config.save_api_key("forced", profile="dev") == config.FILE_STORE
    assert config.get_stored_api_key(profile="dev") == "forced"

    monkeypatch.setenv("DEEPMEDCHEM_CREDENTIAL_STORE", "keyring")
    monkeypatch.setattr(config, "_keyring", lambda: (_ for _ in ()).throw(RuntimeError("none")))
    assert config.get_stored_api_key(profile="dev") is None
    try:
        config.save_api_key("x", profile="dev")
    except config.CredentialError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected CredentialError")
