"""Generic OAuth2 authorization-code flow (RFC 6749) with PKCE (RFC 7636), over httpx."""

from __future__ import annotations

import base64
import hashlib
import math
import secrets
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from alfred_server.connectors.models import Connector, TokenSet

HTTP_TIMEOUT_S = 15.0


class OAuthError(RuntimeError):
    """The provider refused a code exchange or refresh, or answered nonsense.

    `retryable` is True for failures that say nothing about the grant (network error,
    provider 5xx or 429): the same request may work later.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_code_verifier() -> str:
    return secrets.token_urlsafe(64)  # 86 chars, within RFC 7636's 43-128


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OAuth2Client:
    """Talks to providers' token endpoints. `transport` is replaced in tests."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def authorization_url(
        self,
        connector: Connector,
        *,
        client_id: str,
        redirect_uri: str,
        state: str,
        code_verifier: str | None,
    ) -> str:
        assert connector.oauth is not None
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        if connector.scopes:
            params["scope"] = " ".join(connector.scopes)
        if code_verifier is not None:
            params["code_challenge"] = code_challenge(code_verifier)
            params["code_challenge_method"] = "S256"
        params.update(connector.oauth.extra_authorize_params)
        sep = "&" if "?" in connector.oauth.authorize_url else "?"
        return f"{connector.oauth.authorize_url}{sep}{urlencode(params)}"

    def exchange_code(
        self,
        connector: Connector,
        *,
        code: str,
        client_id: str,
        client_secret: str | None,
        redirect_uri: str,
        code_verifier: str | None,
        now: datetime,
    ) -> TokenSet:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
        }
        if client_secret:
            data["client_secret"] = client_secret
        if code_verifier is not None:
            data["code_verifier"] = code_verifier
        return self._token_request(connector, data, now=now, previous_refresh_token=None)

    def refresh(
        self,
        connector: Connector,
        *,
        refresh_token: str,
        client_id: str,
        client_secret: str | None,
        now: datetime,
    ) -> TokenSet:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        if client_secret:
            data["client_secret"] = client_secret
        return self._token_request(connector, data, now=now, previous_refresh_token=refresh_token)

    def _token_request(
        self,
        connector: Connector,
        data: dict[str, str],
        *,
        now: datetime,
        previous_refresh_token: str | None,
    ) -> TokenSet:
        assert connector.oauth is not None
        try:
            with httpx.Client(transport=self._transport, timeout=HTTP_TIMEOUT_S) as client:
                response = client.post(
                    connector.oauth.token_url, data=data, headers={"Accept": "application/json"}
                )
        except httpx.HTTPError as exc:
            raise OAuthError(
                f"{connector.name} token endpoint unreachable: {exc}", retryable=True
            ) from exc
        try:
            body: Any = response.json()
        except ValueError:
            body = {}
        if response.status_code != 200 or not isinstance(body, dict):
            # The error code only: the description may echo request details.
            error = body.get("error") if isinstance(body, dict) else None
            raise OAuthError(
                f"{connector.name} token request failed: HTTP {response.status_code}"
                + (f" ({error})" if error else ""),
                # Provider outage or rate limit: try again. 4xx (invalid_grant...) is final.
                retryable=response.status_code >= 500 or response.status_code == 429,
            )
        access_token = body.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthError(f"{connector.name} token response has no access_token")
        expires_at = None
        seconds = _seconds(body.get("expires_in"))
        if seconds is not None:
            expires_at = now + timedelta(seconds=seconds)
        scope = body.get("scope")
        if isinstance(scope, list):  # some providers send a list
            scope = " ".join(str(s) for s in scope)
        refresh_token = body.get("refresh_token")
        token_type = body.get("token_type")
        return TokenSet(
            access_token=access_token,
            # Providers that don't rotate refresh tokens omit it on refresh: keep the old one.
            refresh_token=refresh_token
            if isinstance(refresh_token, str) and refresh_token
            else previous_refresh_token,
            expires_at=expires_at,
            token_type=token_type if isinstance(token_type, str) and token_type else "Bearer",
            scope=scope if isinstance(scope, str) else None,
        )


def _seconds(value: Any) -> float | None:
    """`expires_in` as seconds; None (no known expiry) if it's missing or not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None
