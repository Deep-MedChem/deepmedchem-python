"""Browser-assisted API-key approval helpers."""

from __future__ import annotations

import time
import webbrowser
from collections.abc import Callable

import httpx

from . import __version__


class LoginError(RuntimeError):
    pass


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
            started = client.post(start_path, json={"client_version": client_version})
            started.raise_for_status()
            payload = started.json()
            device_code = payload["device_code"]
            user_code = payload["user_code"]
            verification_url = payload["verification_uri_complete"]
            interval = max(1.0, float(payload.get("interval", 2)))

            if on_started:
                on_started(user_code, verification_url)
            if open_browser:
                webbrowser.open(verification_url)

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
