"""Confirmation tiers, channels and the pending-confirmation record."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from alfred_server.agents.context import DeviceType
from alfred_server.connectors.models import Risk

# A tool's tier is its connector risk (or Dan's override): `read` runs straight away, `write`
# needs a yes on any attended channel, `irreversible` only an approval in the app.
Tier = Risk
TIER_ORDER: dict[Tier, int] = {"read": 0, "write": 1, "irreversible": 2}

# Where a request (or an approval) came from. `app_ui` is the Alfred app's own confirmation
# screen, proven by a signature from a paired device (ADR 0014; `confirmations/api.py`
# treats an unsigned `app_ui` claim as `text`). `scheduled` is an unattended run, which can
# never approve anything.
Channel = Literal["text", "voice", "app_ui", "scheduled"]

CHANNEL_FOR_DEVICE: dict[DeviceType, Channel] = {
    "text": "text",
    "phone": "voice",
    "scheduled": "scheduled",
}

ConfirmationStatus = Literal["pending", "approved", "denied", "expired", "cancelled"]


def can_approve(tier: Tier, channel: Channel) -> bool:
    """May an approval arriving on `channel` approve a `tier` tool?"""
    if channel == "scheduled":
        return False
    if tier == "irreversible":
        return channel == "app_ui"
    return True


class PendingConfirmation(BaseModel):
    """A paused run waiting for a yes or no. One per paused run, covering all its gated calls.

    `specialist`, `alias` and the request fields are what's needed to rebuild the agent and
    continue the run when the answer comes.
    """

    id: str
    run_id: str
    session_id: str
    specialist: str
    # The model the run paused on. Usually a `ModelAlias`; evals also run on proxy model names
    # (`eval-candidate`), which `Router.resume` replaces with `local-main`.
    alias: str
    tools: list[str]
    summary: str
    tier: Tier
    channel: Channel
    device_type: DeviceType
    device_id: str | None = None
    created_at: datetime
    expires_at: datetime
    status: ConfirmationStatus = "pending"

    def public(self) -> dict[str, object]:
        """What the API and the chat stream show (no routing internals)."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "tools": self.tools,
            "summary": self.summary,
            "tier": self.tier,
            "channel": self.channel,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "status": self.status,
        }
