"""Bearer-token auth shared by every route except `/health`.

The Android app sends `Authorization: Bearer <ALFRED_API_TOKEN>`. Add new routes to the
protected router in `main.py` (or give them `Depends(require_api_token)`) so they get this check.
"""

import secrets
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from alfred_server.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)

TokenCheck = Literal["ok", "invalid", "unconfigured"]


def check_api_token(settings: Settings, presented: str | None) -> TokenCheck:
    """Compare a presented token with `ALFRED_API_TOKEN` (constant time).

    Shared by the HTTP dependency below and the device WebSocket, which can't use it.
    "unconfigured" when no token is set: callers must refuse (fail closed).
    """
    if settings.api_token is None or not settings.api_token.get_secret_value():
        return "unconfigured"
    expected = settings.api_token.get_secret_value()
    if presented is None or not secrets.compare_digest(presented.encode(), expected.encode()):
        return "invalid"
    return "ok"


def require_api_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Reject the request unless it carries the configured API token.

    Fails closed: with no `ALFRED_API_TOKEN` set, every protected request gets a 503.
    """
    result = check_api_token(settings, credentials.credentials if credentials else None)
    if result == "unconfigured":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API token not configured",
        )
    if result == "invalid":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
