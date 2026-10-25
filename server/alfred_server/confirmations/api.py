"""Confirmation and permission routes. Included in the token-protected router in `main.py`.

`GET /confirmations/pending`, `POST /confirmations/{id}` `{approve, channel}`,
`GET /settings/permissions`, `PUT /settings/permissions/{connector}/{tool}` `{risk}`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from alfred_server.agents.context import RequestContext
from alfred_server.agents.router import AgentRunError, Router
from alfred_server.chat import confirmation_service_dependency, router_dependency
from alfred_server.config import Settings, get_settings
from alfred_server.confirmations.models import Channel, Tier, can_approve
from alfred_server.confirmations.service import (
    ConfirmationService,
    ConfirmationsUnavailableError,
    IrreversibleRelaxError,
    cancel_paused_run,
    denied_reply,
)
from alfred_server.devices.api import Devices
from alfred_server.devices.service import ApprovalProof, ApprovalProofError
from alfred_server.models.gateway import GatewayNotConfiguredError

logger = logging.getLogger(__name__)

router = APIRouter()

Service = Annotated[ConfirmationService, Depends(confirmation_service_dependency)]


class ConfirmationAnswer(BaseModel):
    approve: bool
    # Where the answer comes from. `app_ui` (the Alfred app's own confirmation screen) is the
    # only channel that can approve an `irreversible` tool, and an approval only counts as
    # `app_ui` with a valid `proof` signed by a paired device (ADR 0014). Without one it's
    # treated as `text`.
    channel: Literal["text", "app_ui"] = "text"
    proof: ApprovalProof | None = None


class ConfirmationResult(BaseModel):
    message: str
    reply: str | None
    session_id: str
    # Set if the continued run paused again before another gated tool.
    confirmation: dict[str, Any] | None = None


class PermissionChange(BaseModel):
    # None removes the override, back to the connector's default (`ToolPermission.default`).
    risk: Tier | None


class ToolPermission(BaseModel):
    connector_id: str
    tool: str
    declared: Tier
    # The tier without an override: `declared`, or lower if the connector says so.
    default: Tier
    override: Tier | None
    effective: Tier


def _unavailable(exc: Exception) -> HTTPException:
    logger.error("confirmations unavailable: %s", exc)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Confirmations are unavailable")


async def _verified_channel(
    body: ConfirmationAnswer, confirmation_id: str, devices: Devices
) -> Channel:
    """The channel an approval really came from. `app_ui` only with a valid signed proof; a
    claimed `app_ui` without one is a plain `text` answer. A proof that fails is a 403."""
    if body.channel != "app_ui":
        return body.channel
    if body.proof is None:
        return "text"
    try:
        device = await asyncio.to_thread(
            devices.verify_approval, body.proof, confirmation_id, body.approve
        )
    except ApprovalProofError as exc:
        logger.warning("rejected app_ui approval of %s: %s", confirmation_id, exc)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This approval isn't signed by a paired Alfred app."
        ) from exc
    except Exception as exc:
        raise _unavailable(exc) from exc
    logger.info("confirmation %s approved on device %s", confirmation_id, device.name)
    return "app_ui"


@router.get("/confirmations/pending")
async def pending_confirmations(service: Service) -> dict[str, list[dict[str, Any]]]:
    """Confirmations waiting for an answer, oldest first."""
    try:
        items = await asyncio.to_thread(service.pending)
    except Exception as exc:
        raise _unavailable(exc) from exc
    return {"pending": [c.public() for c in items]}


@router.post("/confirmations/{confirmation_id}")
async def answer_confirmation(
    confirmation_id: str,
    body: ConfirmationAnswer,
    service: Service,
    agent_router: Annotated[Router, Depends(router_dependency)],
    settings: Annotated[Settings, Depends(get_settings)],
    devices: Devices,
) -> ConfirmationResult:
    """Approve (the run continues and `reply` is its answer) or deny (it's cancelled)."""
    try:
        c = await asyncio.to_thread(service.get, confirmation_id)
    except Exception as exc:
        raise _unavailable(exc) from exc
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such confirmation")
    if c.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This confirmation was already {c.status}")
    if service.is_expired(c):
        await service.expire_due(agent_router.db)
        raise HTTPException(status.HTTP_409_CONFLICT, "This confirmation has expired")

    if not body.approve:
        if not await asyncio.to_thread(service.resolve, c, "denied"):
            raise HTTPException(status.HTTP_409_CONFLICT, "This confirmation was already answered")
        reply = denied_reply(c)
        await cancel_paused_run(agent_router.db, c, reply)
        return ConfirmationResult(message="Denied.", reply=reply, session_id=c.session_id)

    try:
        tier = await asyncio.to_thread(service.current_tier, c)
    except Exception as exc:
        raise _unavailable(exc) from exc
    channel = await _verified_channel(body, c.id, devices)
    if not can_approve(tier, channel):
        detail = "This action can only be approved in the Alfred app."
        if body.channel == "app_ui":
            detail = "This action needs an approval signed by a paired Alfred app."
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail)
    if not await asyncio.to_thread(service.resolve, c, "approved"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This confirmation was already answered")
    ctx = RequestContext.build(
        timezone=settings.timezone,
        device_type=c.device_type,
        session_id=c.session_id,
        device_id=c.device_id,
    )
    try:
        routed = await agent_router.resume(c, ctx)
    except (ConfirmationsUnavailableError, GatewayNotConfiguredError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except AgentRunError as exc:
        logger.error("resuming run %s failed: %s", c.run_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The agent run failed") from exc
    return ConfirmationResult(
        message="Approved.",
        reply=routed.reply,
        session_id=c.session_id,
        confirmation=routed.confirmation.public() if routed.confirmation else None,
    )


def _permissions(service: ConfirmationService) -> list[ToolPermission]:
    overrides = service.store.overrides()
    return [
        ToolPermission(
            connector_id=connector.id,
            tool=meta.name,
            declared=meta.risk,
            default=meta.tier,
            override=overrides.get((connector.id, meta.name)),
            effective=overrides.get((connector.id, meta.name), meta.tier),
        )
        for connector in service.registry.all()
        for meta in connector.tools
    ]


@router.get("/settings/permissions")
async def get_permissions(service: Service) -> list[ToolPermission]:
    """Every declared tool with its declared risk, Dan's override (if any) and the result."""
    try:
        return await asyncio.to_thread(_permissions, service)
    except Exception as exc:
        raise _unavailable(exc) from exc


@router.put("/settings/permissions/{connector_id}/{tool}")
async def put_permission(
    connector_id: str, tool: str, body: PermissionChange, service: Service
) -> ToolPermission:
    """Relax or tighten one tool (`{"risk": "read" | "write" | "irreversible"}`), or reset it
    to its default (`{"risk": null}`). Applies from the next request.

    A tool that's `irreversible` now can't be relaxed here (403): the shared API token would
    otherwise get round signed approvals. Use `python -m alfred_server.confirmations set`."""
    connector = service.registry.get(connector_id)
    meta = next((t for t in connector.tools if t.name == tool), None) if connector else None
    if connector is None or meta is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such connector tool")
    try:
        await asyncio.to_thread(service.set_override, connector_id, tool, body.risk)
    except IrreversibleRelaxError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "An irreversible tool can only be relaxed on the server "
            "(python -m alfred_server.confirmations set).",
        ) from exc
    except Exception as exc:
        raise _unavailable(exc) from exc
    return ToolPermission(
        connector_id=connector_id,
        tool=tool,
        declared=meta.risk,
        default=meta.tier,
        override=body.risk,
        effective=body.risk or meta.tier,
    )
