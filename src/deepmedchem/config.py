"""Configuration and credential providers for the DeepMedChem platform SDK."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

DEFAULT_API_URL = "https://cheese-new-api.deepmedchem.com"
DEFAULT_WEB_URL = "https://cheese-new.deepmedchem.com"
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


CredentialSource = CredentialProvider | Callable[[], str | None]


@dataclass(frozen=True)
class Config:
    api_url: str = DEFAULT_API_URL
    web_url: str = DEFAULT_WEB_URL


def config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "deepmedchem" / "config.json"


def load_config() -> Config:
    path = config_path()
    try:
        payload = json.loads(path.read_text()) if path.is_file() else {}
    except (OSError, ValueError):
        payload = {}
    api_url = (
        os.environ.get("DEEPMEDCHEM_API_URL")
        or os.environ.get("DMC_API_URL")
        or payload.get("api_url")
        or DEFAULT_API_URL
    )
    web_url = (
        os.environ.get("DEEPMEDCHEM_WEB_URL")
        or payload.get("web_url")
        or DEFAULT_WEB_URL
    )
    return Config(api_url=str(api_url).rstrip("/"), web_url=str(web_url).rstrip("/"))


def save_config(config: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"api_url": config.api_url, "web_url": config.web_url}, indent=2) + "\n"
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _keyring():
    try:
        import keyring
    except ImportError as error:
        raise CredentialError(
            "OS credential storage requires 'deepmedchem[auth]'."
        ) from error
    return keyring


def get_stored_api_key(*, include_legacy: bool = True) -> str | None:
    try:
        keyring = _keyring()
    except CredentialError:
        return None
    value = keyring.get_password(SERVICE, ACCOUNT)
    if value or not include_legacy:
        return value
    return keyring.get_password(LEGACY_SERVICE, LEGACY_ACCOUNT)


def save_api_key(api_key: str) -> None:
    if not api_key or not api_key.strip():
        raise ValueError("api_key must not be empty")
    _keyring().set_password(SERVICE, ACCOUNT, api_key.strip())


def delete_api_key(*, include_legacy: bool = False) -> None:
    keyring = _keyring()
    targets = [(SERVICE, ACCOUNT)]
    if include_legacy:
        targets.append((LEGACY_SERVICE, LEGACY_ACCOUNT))
    for service, account in targets:
        try:
            keyring.delete_password(service, account)
        except keyring.errors.PasswordDeleteError:
            pass


def migrate_legacy_api_key() -> bool:
    """Copy an existing Navigator credential into the shared SDK keyring entry."""

    keyring = _keyring()
    if keyring.get_password(SERVICE, ACCOUNT):
        return False
    legacy = keyring.get_password(LEGACY_SERVICE, LEGACY_ACCOUNT)
    if not legacy:
        return False
    keyring.set_password(SERVICE, ACCOUNT, legacy)
    return True


def resolve_api_key(
    api_key: str | None = None,
    credential_provider: CredentialSource | None = None,
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
    return get_stored_api_key()
