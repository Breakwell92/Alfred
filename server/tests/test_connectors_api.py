from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from alfred_server.connectors import service as connector_service
from alfred_server.connectors.service import ConnectorService, get_connector_service
from alfred_server.main import create_app
from tests.connector_helpers import FakeProvider, make_service

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def service(provider: FakeProvider) -> ConnectorService:
    return make_service(provider=provider)


@pytest.fixture
def client(service: ConnectorService, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    monkeypatch.setenv("ALFRED_PUBLIC_URL", "http://glados:8000/")
    app = create_app()
    app.dependency_overrides[get_connector_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/connectors"),
        ("PUT", "/connectors/time"),
        ("DELETE", "/connectors/acme/auth"),
        ("GET", "/connectors/acme/auth/start"),
    ],
)
def test_routes_require_token(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path).status_code == 401
    assert client.request(method, path, headers={"Authorization": "Bearer no"}).status_code == 401


def test_list_shows_status_and_tool_metadata(client: TestClient) -> None:
    body = client.get("/connectors", headers=AUTH).json()

    by_id = {c["id"]: c for c in body}
    assert by_id["time"]["status"] == "disabled"
    assert by_id["time"]["tools"] == [
        {
            "name": "get_time",
            "description": "Current local date and time for the user.",
            "risk": "read",
            "privacy": "cloud_ok",
        }
    ]
    assert by_id["acme"]["auth_type"] == "oauth2"
    assert by_id["acme"]["scopes"] == ["items.read", "items.write"]


def test_enable_disable(client: TestClient) -> None:
    on = client.put("/connectors/time", json={"enabled": True}, headers=AUTH)
    assert on.status_code == 200
    assert on.json()["status"] == "connected"

    off = client.put("/connectors/time", json={"enabled": False}, headers=AUTH)
    assert off.json()["status"] == "disabled"


def test_api_key_stored_but_never_returned(client: TestClient) -> None:
    response = client.put(
        "/connectors/keyed", json={"enabled": True, "api_key": "sk-secret-9"}, headers=AUTH
    )

    assert response.status_code == 200
    assert response.json()["status"] == "connected"
    assert response.json()["has_api_key"] is True
    assert "sk-secret-9" not in response.text
    assert "sk-secret-9" not in client.get("/connectors", headers=AUTH).text


def test_unknown_connector_404(client: TestClient) -> None:
    assert client.put("/connectors/nope", json={}, headers=AUTH).status_code == 404
    assert client.delete("/connectors/nope/auth", headers=AUTH).status_code == 404
    assert client.get("/connectors/nope/auth/start", headers=AUTH).status_code == 404


def test_wrong_kind_of_secret_422(client: TestClient) -> None:
    assert client.put("/connectors/time", json={"api_key": "x"}, headers=AUTH).status_code == 422
    assert (
        client.put(
            "/connectors/keyed", json={"oauth_client": {"client_id": "x"}}, headers=AUTH
        ).status_code
        == 422
    )
    assert client.put("/connectors/time", json={"bogus": 1}, headers=AUTH).status_code == 422


def test_secrets_refused_without_vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    app = create_app()
    service = make_service(key=None)
    app.dependency_overrides[get_connector_service] = lambda: service
    client = TestClient(app)

    response = client.put("/connectors/keyed", json={"api_key": "k"}, headers=AUTH)

    assert response.status_code == 503
    assert "ALFRED_VAULT_KEY" in response.json()["detail"]
    assert client.put("/connectors/time", json={"enabled": True}, headers=AUTH).status_code == 200


def test_oauth_flow_through_the_api(client: TestClient, provider: FakeProvider) -> None:
    assert client.get("/connectors/acme/auth/start", headers=AUTH).status_code == 422  # no client

    client.put(
        "/connectors/acme",
        json={"enabled": True, "oauth_client": {"client_id": "cid", "client_secret": "cs"}},
        headers=AUTH,
    )
    start = client.get("/connectors/acme/auth/start", headers=AUTH)
    assert start.status_code == 200
    params = parse_qs(urlsplit(start.json()["url"]).query)
    assert params["redirect_uri"] == ["http://glados:8000/connectors/acme/auth/callback"]
    state = params["state"][0]

    # The callback carries no API token: the provider redirects the browser here.
    done = client.get(f"/connectors/acme/auth/callback?code=abc&state={state}")
    assert done.status_code == 200
    assert "Acme connected" in done.text
    assert provider.requests[-1]["code"] == "abc"

    info = {c["id"]: c for c in client.get("/connectors", headers=AUTH).json()}["acme"]
    assert info["status"] == "connected"
    assert info["token_expires_at"] is not None
    assert "access-1" not in str(info)

    replay = client.get(f"/connectors/acme/auth/callback?code=abc&state={state}")
    assert replay.status_code == 400

    cleared = client.delete("/connectors/acme/auth", headers=AUTH)
    assert cleared.json()["status"] == "needs_auth"


def test_callback_rejects_missing_or_forged_state(client: TestClient) -> None:
    assert client.get("/connectors/acme/auth/callback?code=abc").status_code == 400
    assert client.get("/connectors/acme/auth/callback?code=abc&state=forged").status_code == 400


def test_callback_provider_error_consumes_state(
    client: TestClient, service: ConnectorService
) -> None:
    service.update("acme", client_id="cid")
    url = client.get("/connectors/acme/auth/start", headers=AUTH).json()["url"]
    state = parse_qs(urlsplit(url).query)["state"][0]

    denied = client.get(f"/connectors/acme/auth/callback?error=<b>access_denied</b>&state={state}")

    assert denied.status_code == 400
    assert "<b>" not in denied.text  # escaped
    assert client.get(f"/connectors/acme/auth/callback?code=x&state={state}").status_code == 400


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        ("access_denied", "The provider said: access_denied."),
        ("Call 0800 123 456 to verify your account", "complete the sign-in"),
        ("<b>access_denied</b>", "complete the sign-in"),
    ],
)
def test_callback_echoes_only_error_codes(client: TestClient, error: str, shown: str) -> None:
    response = client.get("/connectors/acme/auth/callback", params={"error": error, "state": "s"})

    assert response.status_code == 400
    assert shown in response.text
    if error != "access_denied":
        assert error not in response.text


class FakeMcp:
    closed = False

    async def close(self) -> None:
        self.closed = True


def test_shutdown_closes_mcp_sessions_without_vault_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-secret connectors can be stdio MCP servers: they're stopped at shutdown too."""
    service = make_service(key=None)
    fake = FakeMcp()
    service._mcp["demo"] = ("fingerprint", fake)  # type: ignore[assignment]
    monkeypatch.setattr(connector_service, "build_connector_service", lambda: service)
    get_connector_service.cache_clear()
    try:
        with TestClient(create_app()):
            assert get_connector_service() is service
        assert fake.closed
        assert service._mcp == {}
    finally:
        get_connector_service.cache_clear()


def test_shutdown_does_not_build_an_unused_service(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> ConnectorService:
        raise AssertionError("built at shutdown")

    monkeypatch.setattr(connector_service, "build_connector_service", fail)
    get_connector_service.cache_clear()
    with TestClient(create_app()):
        pass
