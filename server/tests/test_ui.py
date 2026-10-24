"""The web chat page's static files (`/ui/chat`): open, locked down, no secrets."""

import pytest
from fastapi.testclient import TestClient

from alfred_server.main import create_app

TOKEN = "ui-test-token-not-a-secret"

FILES = {
    "/ui/chat": "text/html",
    "/ui/chat.js": "text/javascript",
    "/ui/chat.css": "text/css",
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    return TestClient(create_app())


@pytest.mark.parametrize(("path", "media_type"), FILES.items())
def test_served_without_a_token(client: TestClient, path: str, media_type: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media_type)


@pytest.mark.parametrize("path", FILES)
def test_locked_down_and_uncached(client: TestClient, path: str) -> None:
    headers = client.get(path).headers

    csp = headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", FILES)
def test_no_secrets_in_the_page(client: TestClient, path: str) -> None:
    assert TOKEN not in client.get(path).text


def test_page_loads_its_script_and_styles(client: TestClient) -> None:
    html = client.get("/ui/chat").text

    assert '<script src="/ui/chat.js"' in html
    assert 'href="/ui/chat.css"' in html
    assert "<script>" not in html  # no inline script: the CSP would block it


def test_script_uses_the_protected_api_with_the_token(client: TestClient) -> None:
    script = client.get("/ui/chat.js").text

    assert '"/chat/stream"' in script
    assert '"Bearer " + token' in script
    assert 'device_type: "text"' in script
    assert "innerHTML" not in script  # model output is only ever set as text


def test_api_still_needs_the_token(client: TestClient) -> None:
    assert client.post("/chat/stream", json={"text": "Hi"}).status_code == 401
    assert client.get("/confirmations/pending").status_code in (401, 404)


def test_unknown_ui_paths_are_not_served(client: TestClient) -> None:
    assert client.get("/ui/routes.py").status_code == 404
    assert client.get("/ui/../config.py").status_code == 404
