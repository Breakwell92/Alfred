"""`RequestContext`: who is asking, from where and when, passed into every agent run.

Every request (text, phone, scheduled) builds one and the agent factory renders it into the
agent's system message (`to_prompt()`), so agents can answer "what time is it?", know which
room "the lights" means, and later pick a persona.
"""

from datetime import UTC, datetime
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

DeviceType = Literal["phone", "text", "scheduled"]


class RequestContext(BaseModel):
    """Per-request facts injected into agent instructions as structured context."""

    now: datetime = Field(description="Time the request arrived, in the user's timezone.")
    timezone: str = Field(description="User's IANA timezone, e.g. Europe/London.")
    device_type: DeviceType
    session_id: str = Field(description="Conversation id; turns with the same id share history.")
    device_id: str | None = None
    location: str | None = None
    active_room: str | None = None
    persona_id: str = "default"

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        if _zone(value) is None:
            raise ValueError(f"unknown timezone {value!r}")
        return value

    @field_validator("now")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return value

    @classmethod
    def build(
        cls, *, timezone: str, device_type: DeviceType, session_id: str, **fields: object
    ) -> Self:
        """Context for a request arriving now, with `now` in the user's timezone."""
        # An unknown zone falls back to UTC here and is then rejected by the validator.
        now = datetime.now(_zone(timezone) or UTC)
        return cls.model_validate(
            {
                "now": now,
                "timezone": timezone,
                "device_type": device_type,
                "session_id": session_id,
                **fields,
            }
        )

    def to_prompt(self) -> str:
        """Render as a `<request_context>` block, leaving out fields that aren't set."""
        lines = [
            f"current_time: {self.now.isoformat(timespec='seconds')} ({_long_date(self.now)})",
            f"timezone: {self.timezone}",
            f"device_type: {self.device_type}",
        ]
        optional = {
            "device_id": self.device_id,
            "location": self.location,
            "active_room": self.active_room,
        }
        lines += [f"{key}: {value}" for key, value in optional.items() if value]
        lines += [f"persona_id: {self.persona_id}", f"session_id: {self.session_id}"]
        return "<request_context>\n" + "\n".join(lines) + "\n</request_context>"


def _zone(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _long_date(now: datetime) -> str:
    # strftime's %-d isn't portable (Windows), so build "Saturday 24 October 2026" by hand.
    return f"{now:%A} {now.day} {now:%B %Y}"
