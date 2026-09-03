"""Browser-assisted API-key approval helpers."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping

import httpx

from . import __version__


class LoginError(RuntimeError):
    pass


def can_open_browser(environ: Mapping[str, str] | None = None, platform: str | None = None) -> bool:
    """Return whether opening a graphical browser on this host is plausible.

    Headless servers, containers, and SSH sessions have no display. Python's
    ``webbrowser`` would then fall back to a text browser (lynx, w3m) that takes
    over the terminal, or silently do nothing, so the CLI prints the URL and
    lets the user approve the login from any other device instead.
    """

    env = os.environ if environ is None else environ
    system = sys.platform if platform is None else platform
    if env.get("DEEPMEDCHEM_NO_BROWSER"):
        return False
    if system.startswith(("darwin", "win", "cygwin")):
        return True
    if system.startswith("linux") and "microsoft" in _kernel_release().lower():
        return True  # WSL hands URLs to the Windows browser
    if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY") or env.get("MIR_SOCKET"):
        return True
    if env.get("BROWSER"):
        return True
    return False


def _kernel_release() -> str:
    try:
        return os.uname().release
    except (AttributeError, OSError):
        return ""


def semver_compatible_version(version: str) -> str:
    """Rewrite a PEP 440 version (0.2.0b2, 1.0.0.post1) into a semver-style one.

    The login service validates ``client_version`` against a semver pattern; a
    plain Python pre-release such as ``0.2.0b2`` would be rejected with 422.
    Attribution headers still carry the exact package version.
    """

    return re.sub(r"^(\d+\.\d+\.\d+)\.?(?=[A-Za-z])", r"\1-", version.strip())


def open_browser_safely(url: str) -> None:
    """Open ``url`` without letting a slow or broken browser block the login.

    ``webbrowser.open`` waits for a ``$BROWSER`` helper to exit, and VS Code's
    remote-terminal helper can hang on a headless host, so an explicit
    ``$BROWSER`` is launched detached with its file descriptors closed. Any
    other launcher runs on a daemon thread; polling for approval starts
    immediately either way.
    """

    command = os.environ.get("BROWSER")
    if command:
        try:
            argv = shlex.split(command) if os.name != "nt" else [command]
            argv = [part.replace("%s", url) for part in argv]
            if url not in argv:
                argv.append(url)
            subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            pass
        return

    def launch() -> None:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=launch, name="deepmedchem-open-browser", daemon=True).start()


def browser_login(
    web_url: str,
    *,
    application: str,
    application_version: str | None = None,
    start_path: str = "/api/navigator/login/start",
    poll_path: str = "/api/navigator/login/poll",
    open_browser: bool = True,
    timeout: float = 600,
    transport=None,
    sleep: Callable[[float], None] = time.sleep,
    on_started: Callable[[str, str], None] | None = None,
) -> tuple[str, str, str]:
    """Complete a one-time browser approval flow and return its API key."""

    client_version = application_version or __version__
    headers = {
        "x-dmc-client": application,
        "x-dmc-client-version": client_version,
        "x-dmc-sdk-version": __version__,
        "user-agent": f"{application}/{client_version} deepmedchem/{__version__}",
    }
    try:
        with httpx.Client(
            base_url=web_url.rstrip("/"), headers=headers, timeout=30, transport=transport
        ) as client:
            started = client.post(
                start_path, json={"client_version": semver_compatible_version(client_version)}
            )
            started.raise_for_status()
            payload = started.json()
            device_code = payload["device_code"]
            user_code = payload["user_code"]
            verification_url = payload["verification_uri_complete"]
            interval = max(1.0, float(payload.get("interval", 2)))

            if on_started:
                on_started(user_code, verification_url)
            if open_browser:
                open_browser_safely(verification_url)

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                response = client.post(poll_path, json={"device_code": device_code})
                if response.status_code == 200:
                    result = response.json()
                    return result["api_key"], user_code, verification_url
                if response.status_code not in {202, 404, 410}:
                    response.raise_for_status()
                if response.status_code in {404, 410}:
                    detail = response.json().get("error", "login session is invalid or expired")
                    raise LoginError(str(detail))
                sleep(interval)
    except (httpx.HTTPError, KeyError, ValueError) as error:
        raise LoginError(f"DeepMedChem login failed: {error}") from error
    raise LoginError("DeepMedChem login timed out")
