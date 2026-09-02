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

    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
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
