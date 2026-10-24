"""Bearer-token auth shared by every route except `/health`.

The Android app sends `Authorization: Bearer <ALFRED_API_TOKEN>`. Add new routes to the
protected router in `main.py` (or give them `Depends(require_api_token)`) so they get this check.
"""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from alfred_server.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


def require_api_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Reject the request unless it carries the configured API token.

    Fails closed: with no `ALFRED_API_TOKEN` set, every protected request gets a 503.
    """
    if settings.api_token is None or not settings.api_token.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API token not configured",
        )
    expected = settings.api_token.get_secret_value()
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode(), expected.encode()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
