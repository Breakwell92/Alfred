"""Temporary "hello" agent, registered as the router's `general` specialist until real ones exist.

It has one trivial tool, `get_time`, to prove tool calling works end to end on the local model,
and the dummy `send_test_message` (a `write` tool), to demonstrate confirmation gating (STU-57).
"""

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from agno.agent import Agent
from agno.db.base import AsyncBaseDb, BaseDb
from agno.models.base import Model

from alfred_server.agents.base import build_agent
from alfred_server.agents.context import RequestContext
from alfred_server.config import Settings
from alfred_server.connectors.builtin.test_messages import send_test_message

ECHO_AGENT_NAME = "echo"


def make_get_time(timezone: str) -> Callable[[], str]:
    """A `get_time` tool that reports the current time in `timezone`."""

    def get_time() -> str:
        """Get the current local date and time for the user.

        Returns:
            The current time, e.g. "14:05 on Saturday 24 October 2026 (Europe/London)".
        """
        now = datetime.now(ZoneInfo(timezone))
        return f"{now:%H:%M} on {now:%A} {now.day} {now:%B %Y} ({timezone})"

    return get_time


def build_echo_agent(
    ctx: RequestContext,
    *,
    db: BaseDb | AsyncBaseDb,
    settings: Settings | None = None,
    model: Model | None = None,
) -> Agent:
    return build_agent(
        ECHO_AGENT_NAME,
        ctx=ctx,
        alias="local-main",
        db=db,
        instructions=[
            "When asked the time or date, call get_time and answer from its result.",
            "When asked to send a test message, call send_test_message.",
        ],
        tools=[make_get_time(ctx.timezone), send_test_message],
        settings=settings,
        model=model,
    )
