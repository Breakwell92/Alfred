import pytest
from fastapi.testclient import TestClient

from alfred_server.main import create_app

TOKEN = "test-token-not-a-secret"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    return TestClient(create_app())


def test_me_with_valid_token(client: TestClient) -> None:
    response = client.get("/me", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_me_with_wrong_token(client: TestClient) -> None:
    response = client.get("/me", headers={"Authorization": "Bearer nope"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_me_without_token(client: TestClient) -> None:
    assert client.get("/me").status_code == 401


def test_me_with_non_bearer_scheme(client: TestClient) -> None:
    response = client.get("/me", headers={"Authorization": f"Basic {TOKEN}"})

    assert response.status_code == 401


@pytest.mark.parametrize("configured", [None, ""])
def test_me_fails_closed_without_configured_token(
    monkeypatch: pytest.MonkeyPatch, configured: str | None
) -> None:
    if configured is not None:
        monkeypatch.setenv("ALFRED_API_TOKEN", configured)
    client = TestClient(create_app())

    response = client.get("/me", headers={"Authorization": "Bearer anything"})

    assert response.status_code == 503


def test_health_needs_no_token(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_are_disabled(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404
