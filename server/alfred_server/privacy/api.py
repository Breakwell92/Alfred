"""Privacy REST routes. Included in the token-protected router in `main.py`."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from alfred_server.privacy.models import PrivacySettings
from alfred_server.privacy.service import (
    PrivacyService,
    PrivacyUnavailableError,
    UnknownConnectorError,
    get_privacy_service,
)

router = APIRouter()

Service = Annotated[PrivacyService, Depends(get_privacy_service)]


@router.get("/settings/privacy")
def get_privacy_settings(service: Service) -> PrivacySettings:
    try:
        return service.settings()
    except PrivacyUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.put("/settings/privacy")
def put_privacy_settings(settings: PrivacySettings, service: Service) -> PrivacySettings:
    """Replace the privacy settings: the global switch and the per-connector overrides.

    A full replacement, so both fields must be given (422 otherwise): a partial body would
    silently reset what it leaves out. GET, edit, PUT.
    """
    missing = [
        name for name in PrivacySettings.model_fields if name not in settings.model_fields_set
    ]
    if missing:
        raise HTTPException(
            422, f"PUT replaces all privacy settings; missing: {', '.join(missing)}"
        )
    try:
        return service.save_settings(settings)
    except UnknownConnectorError as exc:
        raise HTTPException(422, str(exc)) from exc
    except PrivacyUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
