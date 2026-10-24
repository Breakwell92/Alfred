from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from alfred_server.connectors.oauth import code_challenge
from alfred_server.connectors.service import (
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorService,
)
from tests.connector_helpers import Clock, FakeProvider, make_service

REDIRECT = "http://glados:8000/connectors/acme/auth/callback"


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(provider: FakeProvider, clock: Clock) -> ConnectorService:
    service = make_service(provider=provider, clock=clock)
    service.update("acme", enabled=True, client_id="client-1", client_secret="secret-1")
    return service


def start(service: ConnectorService) -> dict[str, str]:
    url = service.start_auth("acme", REDIRECT)
    assert url.startswith("https://auth.acme.test/authorize?")
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def sign_in(service: ConnectorService) -> None:
    params = start(service)
    service.complete_auth("acme", code="the-code", state=params["state"])


def test_start_builds_provider_url_with_pkce(service: ConnectorService) -> None:
    params = start(service)

    assert params["response_type"] == "code"
    assert params["client_id"] == "client-1"
    assert params["redirect_uri"] == REDIRECT
    assert params["scope"] == "items.read items.write"
    assert params["access_type"] == "offline"
    assert params["code_challenge_method"] == "S256"
    assert len(params["state"]) >= 32
    assert "secret-1" not in str(params)


def test_start_needs_a_client_id() -> None:
    service = make_service()
    with pytest.raises(ConnectorConfigError, match="client_id"):
        service.start_auth("acme", REDIRECT)


def test_start_rejects_non_oauth_connector(service: ConnectorService) -> None:
    with pytest.raises(ConnectorConfigError):
        service.start_auth("keyed", REDIRECT)


def test_callback_exchanges_code_and_stores_tokens(
    service: ConnectorService, provider: FakeProvider, clock: Clock
) -> None:
    assert service.info("acme").status == "needs_auth"
    params = start(service)

    info = service.complete_auth("acme", code="the-code", state=params["state"])

    assert info.status == "connected"
    assert info.token_expires_at == clock.now + timedelta(seconds=3600)
    exchange = provider.requests[-1]
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code"] == "the-code"
    assert exchange["redirect_uri"] == REDIRECT
    assert exchange["client_secret"] == "secret-1"
    # PKCE: the verifier sent now matches the challenge sent with the authorize URL.
    assert code_challenge(exchange["code_verifier"]) == params["code_challenge"]
    creds = service.credentials_for("acme")
    assert creds is not None and creds.access_token == "access-1"


def test_state_works_once(service: ConnectorService) -> None:
    params = start(service)
    service.complete_auth("acme", code="c", state=params["state"])

    with pytest.raises(ConnectorAuthError, match="already used"):
        service.complete_auth("acme", code="c", state=params["state"])


def test_unknown_state_rejected(service: ConnectorService) -> None:
    with pytest.raises(ConnectorAuthError):
        service.complete_auth("acme", code="c", state="forged")


def test_state_for_another_connector_rejected(service: ConnectorService) -> None:
    params = start(service)
    with pytest.raises(ConnectorAuthError):
        service.complete_auth("keyed", code="c", state=params["state"])


def test_expired_state_rejected(service: ConnectorService, clock: Clock) -> None:
    params = start(service)
    clock.advance(minutes=11)

    with pytest.raises(ConnectorAuthError, match="expired"):
        service.complete_auth("acme", code="c", state=params["state"])


def test_provider_refusal_is_an_auth_error(
    service: ConnectorService, provider: FakeProvider
) -> None:
    params = start(service)
    provider.fail_with = 400

    with pytest.raises(ConnectorAuthError, match="invalid_grant"):
        service.complete_auth("acme", code="c", state=params["state"])
    assert service.info("acme").status == "needs_auth"


def test_refresh_due_renews_tokens_before_expiry(
    service: ConnectorService, provider: FakeProvider, clock: Clock
) -> None:
    sign_in(service)

    assert service.refresh_due() == []  # an hour left
    clock.advance(minutes=56)  # 4 minutes left: inside the 5-minute margin
    assert service.refresh_due() == ["acme"]

    refresh = provider.requests[-1]
    assert refresh["grant_type"] == "refresh_token"
    assert refresh["refresh_token"] == "refresh-1"
    creds = service.credentials_for("acme")
    assert creds is not None and creds.access_token == "access-2"
    assert service.info("acme").token_expires_at is not None


def test_refresh_keeps_refresh_token_when_provider_doesnt_rotate(
    service: ConnectorService, provider: FakeProvider, clock: Clock
) -> None:
    sign_in(service)
    provider.rotate_refresh_token = False
    clock.advance(minutes=56)
    service.refresh_due()
    clock.advance(minutes=56)
    service.refresh_due()

    assert provider.requests[-1]["refresh_token"] == "refresh-1"
    creds = service.credentials_for("acme")
    assert creds is not None and creds.access_token == "access-3"


def test_refresh_failure_marks_needs_reauth(
    service: ConnectorService, provider: FakeProvider, clock: Clock
) -> None:
    sign_in(service)
    provider.fail_with = 400
    clock.advance(minutes=56)

    assert service.refresh_due() == []
    info = service.info("acme")
    assert info.status == "needs_reauth"
    assert info.last_error and "invalid_grant" in info.last_error
    assert service.credentials_for("acme") is None

    # Not retried in a loop: a human has to sign in again.
    calls = len(provider.requests)
    service.refresh_due()
    assert len(provider.requests) == calls

    provider.fail_with = None
    sign_in(service)
    assert service.info("acme").status == "connected"


def test_expired_token_refreshed_on_use(
    service: ConnectorService, provider: FakeProvider, clock: Clock
) -> None:
    sign_in(service)
    clock.advance(hours=2)  # the refresher missed it (e.g. the server was off)

    creds = service.credentials_for("acme")

    assert creds is not None and creds.access_token == "access-2"


def test_disabled_connector_not_refreshed(
    service: ConnectorService, provider: FakeProvider, clock: Clock
) -> None:
    sign_in(service)
    service.update("acme", enabled=False)
    clock.advance(minutes=56)

    assert service.refresh_due() == []


def test_delete_auth_forgets_tokens_but_keeps_client(service: ConnectorService) -> None:
    sign_in(service)

    info = service.delete_auth("acme")

    assert info.status == "needs_auth"
    assert info.has_oauth_client
    assert info.token_expires_at is None
    assert service.credentials_for("acme") is None


@pytest.mark.parametrize("failure", ["unreachable", 503, 429])
def test_transient_refresh_failure_retries_next_tick(
    service: ConnectorService, provider: FakeProvider, clock: Clock, failure: str | int
) -> None:
    sign_in(service)
    if failure == "unreachable":
        provider.unreachable = True
    else:
        provider.fail_with = int(failure)
    clock.advance(minutes=56)

    assert service.refresh_due() == []
    info = service.info("acme")
    assert info.status == "connected"  # the grant may still be fine
    assert info.last_error

    provider.unreachable, provider.fail_with = False, None
    assert service.refresh_due() == ["acme"]  # retried on the next tick
    info = service.info("acme")
    assert (info.status, info.last_error) == ("connected", None)
    creds = service.credentials_for("acme")
    assert creds is not None and creds.access_token == "access-2"


def test_transient_failure_with_expired_token_loads_nothing(
    service: ConnectorService, provider: FakeProvider, clock: Clock
) -> None:
    sign_in(service)
    provider.fail_with = 503
    clock.advance(hours=2)

    assert service.credentials_for("acme") is None
    assert service.info("acme").status == "connected"


@pytest.mark.parametrize("status", [400, 401])
def test_rejected_grant_needs_reauth(
    service: ConnectorService, provider: FakeProvider, clock: Clock, status: int
) -> None:
    sign_in(service)
    provider.fail_with = status
    clock.advance(minutes=56)

    service.refresh_due()

    assert service.info("acme").status == "needs_reauth"


@pytest.mark.parametrize("expires_in", ["3600s", "never", None, [1], -5, True])
def test_bad_expires_in_means_no_known_expiry(
    service: ConnectorService, provider: FakeProvider, expires_in: object
) -> None:
    provider.extra = {"expires_in": expires_in}
    sign_in(service)

    info = service.info("acme")
    assert info.status == "connected"
    assert info.token_expires_at is None


def test_numeric_string_expires_in_and_list_scope(
    service: ConnectorService, provider: FakeProvider, clock: Clock
) -> None:
    provider.extra = {"expires_in": "1800", "scope": ["items.read", "items.write"]}
    sign_in(service)

    assert service.info("acme").token_expires_at == clock.now + timedelta(seconds=1800)
    row = service.store.tokens("acme")
    assert row is not None
    assert service.vault.decrypt(row.ciphertext)["scope"] == "items.read items.write"
