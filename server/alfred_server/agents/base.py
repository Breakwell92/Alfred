"""Agent factory: every Alfred agent is built here so they share the same defaults.

Agents are cheap to build and are built per request, with that request's `RequestContext`:
the context goes into the system message and the request's trigger into the LiteLLM call
tags. Specialists (and the router's registry, STU-55) build theirs with `build_agent`.
"""

from collections.abc import Callable, Sequence
from typing import Any

from agno.agent import Agent
from agno.db.base import AsyncBaseDb, BaseDb
from agno.models.base import Model

from alfred_server.agents.context import RequestContext
from alfred_server.config import Settings
from alfred_server.models.gateway import CallMetadata, ModelAlias, gateway_model

# Earlier turns of the session sent with each request (Agno reads them from the db).
HISTORY_RUNS = 10

# A reply of exactly this means "I can't answer this reliably": the router then retries once on
# a cloud model if the budget allows (docs/decisions/0009-router.md). Never shown to the user.
LOW_CONFIDENCE_MARKER = "<<UNSURE>>"

# Shared by every agent. Replies are usually spoken, so no markdown and no lists.
BASE_INSTRUCTIONS: tuple[str, ...] = (
    "You are Alfred, a personal assistant. Replies are usually read aloud by a speech engine.",
    "Be concise: one or two short sentences unless asked for more.",
    "Write plain sentences: no markdown, bullet points, tables, emoji or code blocks.",
    "Use British English. Give times in the user's timezone from the request context.",
    "Use your tools for facts you can look up rather than guessing.",
    f"If you cannot answer reliably, reply with exactly {LOW_CONFIDENCE_MARKER} and nothing else.",
)


def build_agent(
    name: str,
    *,
    ctx: RequestContext,
    alias: ModelAlias,
    db: BaseDb | AsyncBaseDb,
    instructions: Sequence[str] = (),
    tools: Sequence[Callable[..., Any]] = (),
    settings: Settings | None = None,
    model: Model | None = None,
) -> Agent:
    """Build agent `name` on model `alias` with Alfred's shared defaults.

    `model` replaces the gateway model (tests). Raises `GatewayNotConfiguredError` when no
    model is given and `ALFRED_LITELLM_API_KEY` is unset.
    """
    if model is None:
        metadata = CallMetadata(agent=name, trigger=ctx.device_type)
        model = gateway_model(alias, metadata=metadata, settings=settings)
    return Agent(
        name=name,
        model=model,
        db=db,
        tools=list(tools),
        instructions=[*BASE_INSTRUCTIONS, *instructions],
        additional_context=ctx.to_prompt(),
        add_history_to_context=True,
        num_history_runs=HISTORY_RUNS,
        markdown=False,
        timezone_identifier=ctx.timezone,
        # Agno sends usage telemetry to agno.com by default; Alfred keeps everything local.
        telemetry=False,
    )
