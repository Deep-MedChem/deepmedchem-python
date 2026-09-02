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
