from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from alfred_server.agents.context import RequestContext


def test_build_sets_now_in_user_timezone() -> None:
    ctx = RequestContext.build(timezone="Europe/London", device_type="text", session_id="s1")

    assert ctx.now.tzinfo == ZoneInfo("Europe/London")
    assert abs((datetime.now(UTC) - ctx.now).total_seconds()) < 5
    assert ctx.persona_id == "default"
    assert ctx.device_id is ctx.location is ctx.active_room is None


def test_prompt_lists_set_fields_only() -> None:
    ctx = RequestContext(
        now=datetime(2026, 10, 24, 14, 5, tzinfo=ZoneInfo("Europe/London")),
        timezone="Europe/London",
        device_type="phone",
        session_id="s1",
        device_id="pixel-9",
        active_room="kitchen",
    )

    assert ctx.to_prompt() == (
        "<request_context>\n"
        "current_time: 2026-10-24T14:05:00+01:00 (Saturday 24 October 2026)\n"
        "timezone: Europe/London\n"
        "device_type: phone\n"
        "device_id: pixel-9\n"
        "active_room: kitchen\n"
        "persona_id: default\n"
        "session_id: s1\n"
        "</request_context>"
    )


def test_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError):
        RequestContext.build(timezone="Mars/Olympus", device_type="text", session_id="s1")


def test_rejects_naive_time() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        RequestContext(
            now=datetime(2026, 10, 24, 14, 5),
            timezone="Europe/London",
            device_type="text",
            session_id="s1",
        )


def test_rejects_unknown_device_type() -> None:
    with pytest.raises(ValidationError):
        RequestContext.build(timezone="Europe/London", device_type="toaster", session_id="s1")  # type: ignore[arg-type]
