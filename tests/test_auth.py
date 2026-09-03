import httpx

from deepmedchem import __version__
from deepmedchem.auth import browser_login


def test_browser_login_uses_caller_application_and_polls(monkeypatch) -> None:
    polls = 0
    opened = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        assert request.headers["x-dmc-client"] == "navigator-cli"
        assert request.headers["x-dmc-client-version"] == "0.4.0"
        assert request.headers["x-dmc-sdk-version"] == __version__
        assert f"deepmedchem/{__version__}" in request.headers["user-agent"]
        if request.url.path.endswith("/start"):
            return httpx.Response(
                200,
                json={
                    "device_code": "device-secret",
                    "user_code": "CHEE-SE",
                    "verification_uri_complete": "https://cheese.test/login?code=CHEE-SE",
                    "interval": 1,
                },
            )
        polls += 1
        if polls == 1:
            return httpx.Response(202, json={"status": "pending"})
        return httpx.Response(200, json={"api_key": "shared-cheese-key"})

    monkeypatch.setattr("deepmedchem.auth.open_browser_safely", lambda url: opened.append(url))
    token, code, url = browser_login(
        "https://cheese.test",
        application="navigator-cli",
        application_version="0.4.0",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )

    assert token == "shared-cheese-key"
    assert code == "CHEE-SE"
    assert opened == [url]
    assert polls == 2


def test_headless_detection() -> None:
    from deepmedchem.auth import can_open_browser

    assert can_open_browser({}, "darwin") is True
    assert can_open_browser({}, "win32") is True
    assert can_open_browser({}, "linux") is False
    assert can_open_browser({"SSH_CONNECTION": "1"}, "linux") is False
    assert can_open_browser({"DISPLAY": ":0"}, "linux") is True
    assert can_open_browser({"WAYLAND_DISPLAY": "wayland-0"}, "linux") is True
    assert can_open_browser({"BROWSER": "firefox"}, "linux") is True
    assert can_open_browser({"DISPLAY": ":0", "DEEPMEDCHEM_NO_BROWSER": "1"}, "linux") is False


def test_browser_failure_does_not_abort_login(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/start"):
            return httpx.Response(
                201,
                json={
                    "device_code": "d" * 40,
                    "user_code": "ABCD-2345",
                    "verification_uri_complete": "https://cheese.test/navigator/login?code=ABCD-2345",
                    "interval": 1,
                },
            )
        return httpx.Response(200, json={"api_key": "key"})

    def broken_open(url, new=0):
        raise OSError("no browser")

    monkeypatch.setattr("webbrowser.open", broken_open)
    token, _, _ = browser_login(
        "https://cheese.test",
        application="deepmedchem-python",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    assert token == "key"


def test_pep440_versions_are_sent_as_semver() -> None:
    from deepmedchem.auth import semver_compatible_version

    assert semver_compatible_version("0.2.0b2") == "0.2.0-b2"
    assert semver_compatible_version("1.0.0rc1") == "1.0.0-rc1"
    assert semver_compatible_version("1.0.0.post1") == "1.0.0-post1"
    assert semver_compatible_version("1.0.0") == "1.0.0"
    assert semver_compatible_version("1.0.0-rc.1") == "1.0.0-rc.1"
