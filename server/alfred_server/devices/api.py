"""`POST /devices/pair`. Included in the token-protected router in `main.py`.

The token alone isn't enough to pair: the request also needs a one-time code minted on the
server (`python -m alfred_server.devices pair`).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from alfred_server.devices.service import (
    DeviceService,
    PairingError,
    get_device_service,
    load_public_key,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def device_service_dependency() -> DeviceService:
    """Paired devices and pairing codes. Overridden in tests."""
    return get_device_service()


Devices = Annotated[DeviceService, Depends(device_service_dependency)]


class PairRequest(BaseModel):
    code: str
    # Base64 X.509 SubjectPublicKeyInfo (DER) of an EC P-256 key.
    public_key: str


class PairResponse(BaseModel):
    device_id: str
    name: str


@router.post("/devices/pair")
async def pair_device(body: PairRequest, devices: Devices) -> PairResponse:
    """Register the app's approval-signing key with a one-time code from the server CLI."""
    try:
        load_public_key(body.public_key)
    except PairingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    try:
        device = await asyncio.to_thread(devices.pair, body.code, body.public_key)
    except PairingError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except Exception as exc:
        logger.error("pairing unavailable: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Pairing is unavailable") from exc
    logger.info("paired device %s (%s)", device.id, device.name)
    return PairResponse(device_id=device.id, name=device.name)
