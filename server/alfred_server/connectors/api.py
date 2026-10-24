"""Connector REST routes.

`router` goes on the token-protected router in `main.py`. `callback_router` is the one other
open route besides `/health`: the provider redirects the user's browser to it, so it can't
carry the API token. It only accepts a one-time `state` created by the protected
`/auth/start` within the last 10 minutes (plus PKCE), as decided in ADR 0009.
"""

import contextlib
from html import escape
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, SecretStr

from alfred_server.config import Settings, get_settings
from alfred_server.connectors.service import (
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorInfo,
    ConnectorNotFoundError,
    ConnectorService,
    get_connector_service,
)
from alfred_server.connectors.vault import VaultError, VaultUnavailableError

router = APIRouter()
callback_router = APIRouter()

Service = Annotated[ConnectorService, Depends(get_connector_service)]


class OAuthClient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str
    client_secret: SecretStr | None = None


class ConnectorUpdate(BaseModel):
    """`PUT /connectors/{id}`. Every field is optional; omitted ones are left as they are."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    api_key: SecretStr | None = None
    oauth_client: OAuthClient | None = None


class AuthStart(BaseModel):
    url: str


def _not_found(connector_id: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown connector {connector_id!r}")


def _vault_problem(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


def redirect_uri(request: Request, settings: Settings, connector_id: str) -> str:
    """Where the provider sends the browser back. Must match the provider's app settings."""
    base = (settings.public_url or str(request.base_url)).rstrip("/")
    return f"{base}/connectors/{connector_id}/auth/callback"


@router.get("/connectors")
def list_connectors(service: Service) -> list[ConnectorInfo]:
    return service.list_connectors()


@router.put("/connectors/{connector_id}")
def update_connector(connector_id: str, body: ConnectorUpdate, service: Service) -> ConnectorInfo:
    try:
        return service.update(
            connector_id,
            enabled=body.enabled,
            api_key=body.api_key.get_secret_value() if body.api_key else None,
            client_id=body.oauth_client.client_id if body.oauth_client else None,
            client_secret=(
                body.oauth_client.client_secret.get_secret_value()
                if body.oauth_client and body.oauth_client.client_secret
                else None
            ),
        )
    except ConnectorNotFoundError as exc:
        raise _not_found(connector_id) from exc
    except ConnectorConfigError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (VaultError, VaultUnavailableError) as exc:
        raise _vault_problem(exc) from exc


@router.delete("/connectors/{connector_id}/auth")
def delete_connector_auth(connector_id: str, service: Service) -> ConnectorInfo:
    try:
        return service.delete_auth(connector_id)
    except ConnectorNotFoundError as exc:
        raise _not_found(connector_id) from exc
    except VaultUnavailableError as exc:
        raise _vault_problem(exc) from exc


@router.get("/connectors/{connector_id}/auth/start")
def start_connector_auth(
    connector_id: str,
    request: Request,
    service: Service,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthStart:
    """The provider URL to open in a browser to sign in."""
    try:
        url = service.start_auth(connector_id, redirect_uri(request, settings, connector_id))
    except ConnectorNotFoundError as exc:
        raise _not_found(connector_id) from exc
    except ConnectorConfigError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (VaultError, VaultUnavailableError) as exc:
        raise _vault_problem(exc) from exc
    return AuthStart(url=url)


def _page(title: str, message: str, code: int = 200) -> HTMLResponse:
    body = (
        f"<!doctype html><meta charset=utf-8><title>{escape(title)}</title>"
        f"<h1>{escape(title)}</h1><p>{escape(message)}</p>"
    )
    return HTMLResponse(body, status_code=code)


@callback_router.get("/connectors/{connector_id}/auth/callback", response_class=HTMLResponse)
def connector_auth_callback(
    connector_id: str,
    service: Service,
    state: str = "",
    code: str = "",
    error: str = "",
) -> HTMLResponse:
    if not state:
        return _page("Sign-in failed", "The request has no state.", 400)
    if error or not code:
        # Consume the state so it can't be replayed, then report the provider's error code.
        with contextlib.suppress(Exception):  # best effort
            service.store.pop_oauth_state(state)
        return _page("Sign-in failed", f"The provider said: {error or 'no code'}.", 400)
    try:
        info = service.complete_auth(connector_id, code=code, state=state)
    except ConnectorNotFoundError:
        return _page("Sign-in failed", "Unknown connector.", 404)
    except ConnectorAuthError as exc:
        return _page("Sign-in failed", str(exc), 400)
    except (VaultError, VaultUnavailableError) as exc:
        return _page("Sign-in failed", str(exc), 503)
    return _page(f"{info.name} connected", "You can close this page.")
