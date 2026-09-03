"""Named profiles and credential providers for the DeepMedChem SDK."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol, Union, runtime_checkable

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.9/3.10
    import tomli as tomllib
from platformdirs import user_config_path

DEFAULT_API_URL = "https://api.deepmedchem.com"
DEFAULT_WEB_URL = "https://cheese.deepmedchem.com"
DEV_API_URL = "https://api-dev.deepmedchem.com"
DEV_WEB_URL = "https://cheese-new-dev.deepmedchem.com"
SERVICE = "deepmedchem"
ACCOUNT = "default-api-key"
LEGACY_SERVICE = "dmc-navigator"
LEGACY_ACCOUNT = "platform-token"


class CredentialError(RuntimeError):
    """Credential storage could not be accessed or updated."""


@runtime_checkable
class CredentialProvider(Protocol):
    """Protocol implemented by custom API-key providers."""

    def get_api_key(self) -> str | None: ...


# Unlike annotations on functions and classes, a type alias is evaluated at
# runtime on Python 3.9. Keep this expression free of PEP 604 ``|`` unions.
CredentialSource = Union[CredentialProvider, Callable[[], Optional[str]]]


@dataclass(frozen=True)
class Profile:
    api_url: str
    web_url: str


DEFAULT_PROFILES: Mapping[str, Profile] = {
    "default": Profile(api_url=DEFAULT_API_URL, web_url=DEFAULT_WEB_URL),
    "dev": Profile(api_url=DEV_API_URL, web_url=DEV_WEB_URL),
}


@dataclass(frozen=True)
class Config:
    active_profile: str = "default"
    profiles: Mapping[str, Profile] = field(default_factory=lambda: dict(DEFAULT_PROFILES))

    @property
    def api_url(self) -> str:
        return self.profile().api_url

    @property
    def web_url(self) -> str:
        return self.profile().web_url

    def profile(self, name: str | None = None) -> Profile:
        selected = name or self.active_profile
        try:
            return self.profiles[selected]
        except KeyError as error:
            raise ValueError(
                f"Unknown DeepMedChem profile {selected!r}. Add it to {config_path()}."
            ) from error


def config_path() -> Path:
    return user_config_path("deepmedchem", "Deep MedChem") / "config.toml"


def _legacy_config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "deepmedchem" / "config.json"


def _profile_from_payload(payload: Mapping[str, object], fallback: Profile) -> Profile:
    return Profile(
        api_url=str(payload.get("api_url") or fallback.api_url).rstrip("/"),
        web_url=str(payload.get("web_url") or fallback.web_url).rstrip("/"),
    )


def load_config() -> Config:
    path = config_path()
    payload: dict[str, object] = {}
    try:
        if path.is_file():
            payload = tomllib.loads(path.read_text())
        elif _legacy_config_path().is_file():
            legacy = json.loads(_legacy_config_path().read_text())
            payload = {"profiles": {"default": legacy}}
    except (OSError, ValueError, TypeError):
        payload = {}

    configured_profiles = payload.get("profiles", {})
    if not isinstance(configured_profiles, Mapping):
        configured_profiles = {}
    profiles = dict(DEFAULT_PROFILES)
    for name, value in configured_profiles.items():
        if isinstance(name, str) and isinstance(value, Mapping):
            fallback = profiles.get(name, DEFAULT_PROFILES["default"])
            profiles[name] = _profile_from_payload(value, fallback)

    active = str(payload.get("active_profile") or "default")
    if active not in profiles:
        active = "default"

    default = profiles["default"]
    profiles["default"] = Profile(
        api_url=(
            os.environ.get("DEEPMEDCHEM_API_URL")
            or os.environ.get("DMC_API_URL")
            or default.api_url
        ).rstrip("/"),
        web_url=(os.environ.get("DEEPMEDCHEM_WEB_URL") or default.web_url).rstrip("/"),
    )
    return Config(active_profile=active, profiles=profiles)


def save_config(config: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'active_profile = "{config.active_profile}"', ""]
    for name, profile in config.profiles.items():
        lines.extend(
            [
                f"[profiles.{name}]",
                f'api_url = "{profile.api_url}"',
                f'web_url = "{profile.web_url}"',
                "",
            ]
        )
    temporary = path.with_suffix(".tmp")
    temporary.write_text("\n".join(lines))
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def resolve_profile(profile: str | None = None, config: Config | None = None) -> str:
    selected = profile or os.environ.get("DEEPMEDCHEM_PROFILE")
    if selected:
        return selected
    return (config or load_config()).active_profile


def profile_account(profile: str) -> str:
    return f"api-key:{profile}"


def _keyring():
    try:
        import keyring
    except ImportError as error:
        raise CredentialError("OS credential storage requires the 'keyring' package.") from error
    return keyring


def _keyring_get(profile: str, include_legacy: bool) -> str | None:
    try:
        keyring = _keyring()
        value = keyring.get_password(SERVICE, profile_account(profile))
        if value or not include_legacy or profile != "default":
            return value
        value = keyring.get_password(SERVICE, ACCOUNT)
        if not value:
            value = keyring.get_password(LEGACY_SERVICE, LEGACY_ACCOUNT)
        if value:
            keyring.set_password(SERVICE, profile_account(profile), value)
        return value
    except Exception:
        # Includes CredentialError (no keyring package) and backend errors such as
        # keyring.errors.NoKeyringError on headless hosts.
        return None


# --- Encrypted-store-free fallback for headless hosts -------------------------
#
# Servers, containers, and SSH sessions usually have no Secret Service or
# other OS keyring. Rather than failing `deepmedchem login` after the browser
# approval already succeeded, credentials fall back to a 0600 JSON file next
# to config.toml. Set DEEPMEDCHEM_CREDENTIAL_STORE=file to force the file store
# (for example when a keyring backend exists but cannot unlock without a GUI),
# or =keyring to disable the fallback.

FILE_STORE = "file"
KEYRING_STORE = "keyring"


def credentials_path() -> Path:
    return config_path().with_name("credentials.json")


def credential_store_preference() -> str:
    value = (os.environ.get("DEEPMEDCHEM_CREDENTIAL_STORE") or "auto").strip().lower()
    return value if value in {"auto", FILE_STORE, KEYRING_STORE} else "auto"


def _read_file_store() -> dict[str, str]:
    path = credentials_path()
    try:
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        return {}
    keys = payload.get("api_keys") if isinstance(payload, Mapping) else None
    if not isinstance(keys, Mapping):
        return {}
    return {str(k): str(v) for k, v in keys.items() if isinstance(v, str) and v}


def _write_file_store(keys: Mapping[str, str]) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"api_keys": dict(keys)}, indent=2, sort_keys=True) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def get_stored_api_key(*, profile: str = "default", include_legacy: bool = True) -> str | None:
    preference = credential_store_preference()
    if preference != FILE_STORE:
        value = _keyring_get(profile, include_legacy)
        if value:
            return value
    if preference != KEYRING_STORE:
        return _read_file_store().get(profile)
    return None


def save_api_key(api_key: str, *, profile: str = "default") -> str:
    """Persist an API key and return the store that holds it ("keyring" or "file")."""

    if not api_key or not api_key.strip():
        raise ValueError("api_key must not be empty")
    preference = credential_store_preference()
    keyring_error: Exception | None = None
    if preference != FILE_STORE:
        try:
            _keyring().set_password(SERVICE, profile_account(profile), api_key.strip())
        except Exception as error:  # no backend, locked backend, D-Bus missing, ...
            keyring_error = error
        else:
            # A stale file entry must not shadow or outlive the keyring entry.
            stored = _read_file_store()
            if profile in stored:
                del stored[profile]
                _write_file_store(stored)
            return KEYRING_STORE
    if preference == KEYRING_STORE:
        raise CredentialError(
            "No usable OS credential store is available. Set DEEPMEDCHEM_API_KEY "
            "through your environment, unset DEEPMEDCHEM_CREDENTIAL_STORE, or "
            "configure a credential provider."
        ) from keyring_error
    try:
        stored = _read_file_store()
        stored[profile] = api_key.strip()
        _write_file_store(stored)
    except OSError as error:
        raise CredentialError(
            f"Could not write the credential file {credentials_path()}. Set "
            "DEEPMEDCHEM_API_KEY through your environment or configure a credential provider."
        ) from error
    return FILE_STORE


def delete_api_key(*, profile: str = "default", include_legacy: bool = False) -> None:
    removed_from_file = False
    stored = _read_file_store()
    if profile in stored:
        del stored[profile]
        try:
            _write_file_store(stored)
        except OSError as error:
            raise CredentialError(
                f"Could not update the credential file {credentials_path()}."
            ) from error
        removed_from_file = True
    try:
        keyring = _keyring()
        targets = [(SERVICE, profile_account(profile))]
        if include_legacy and profile == "default":
            targets.extend([(SERVICE, ACCOUNT), (LEGACY_SERVICE, LEGACY_ACCOUNT)])
        for service, account in targets:
            try:
                keyring.delete_password(service, account)
            except keyring.errors.PasswordDeleteError:
                pass
    except Exception as error:
        if removed_from_file:
            return
        raise CredentialError(
            "No usable OS credential store is available; no credential was removed."
        ) from error


def delete_all_api_keys(config: Config | None = None) -> None:
    for profile in (config or load_config()).profiles:
        delete_api_key(profile=profile, include_legacy=profile == "default")


def migrate_legacy_api_key() -> bool:
    """Copy a legacy shared credential into the default profile."""

    keyring = _keyring()
    target = profile_account("default")
    if keyring.get_password(SERVICE, target):
        return False
    legacy = keyring.get_password(SERVICE, ACCOUNT) or keyring.get_password(
        LEGACY_SERVICE, LEGACY_ACCOUNT
    )
    if not legacy:
        return False
    keyring.set_password(SERVICE, target, legacy)
    return True


def resolve_api_key(
    api_key: str | None = None,
    credential_provider: CredentialSource | None = None,
    *,
    profile: str = "default",
) -> str | None:
    if api_key:
        return api_key
    value = (
        os.environ.get("DEEPMEDCHEM_API_KEY")
        or os.environ.get("DMC_API_KEY")
        or os.environ.get("CHEESE_API_KEY")
    )
    if value:
        return value
    if credential_provider is not None:
        if isinstance(credential_provider, CredentialProvider):
            value = credential_provider.get_api_key()
        else:
            value = credential_provider()
        if value:
            return value
    return get_stored_api_key(profile=profile)
