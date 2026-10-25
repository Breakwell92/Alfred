"""Is a spoken reply to "Shall I go ahead?" a yes or a no?

`parse_yes_no` (confirmations/service.py) catches plain answers ("yes", "no", "go ahead").
Speech is looser ("yeah go on then", "actually no, leave it"), so when that finds nothing the
gateway asks `local-router`. Only called while a confirmation is pending.
"""

from __future__ import annotations

import logging
from typing import Literal

from agno.agent import Agent
from pydantic import BaseModel

from alfred_server.agents.router import ModelFactory
from alfred_server.confirmations.service import YesNo
from alfred_server.models.gateway import CallMetadata

logger = logging.getLogger(__name__)

YES_NO_AGENT_NAME = "confirm"

_PROMPT = """The user was asked "Shall I go ahead?" about an action, and this is their spoken \
reply. Is it a yes, a no, or something else (a new request, or unclear)? Reply with JSON only, \
e.g. {"answer":"yes"}."""


class _Answer(BaseModel):
    answer: Literal["yes", "no", "other"]


async def classify_yes_no(text: str, model_factory: ModelFactory) -> YesNo | None:
    """ "yes", "no", or None for anything else, including a model failure.

    None counts as "the user moved on", which cancels a pending `write` confirmation: a
    failure never approves anything.
    """
    model = model_factory(
        "local-router",
        CallMetadata(agent=YES_NO_AGENT_NAME, trigger="phone"),
        max_tokens=20,
        temperature=0,
    )
    agent = Agent(
        name=YES_NO_AGENT_NAME,
        model=model,
        instructions=_PROMPT,
        output_schema=_Answer,
        markdown=False,
        telemetry=False,
    )
    try:
        run = await agent.arun(text)
    except Exception:
        logger.exception("yes/no check failed; treating the reply as neither")
        return None
    if not isinstance(run.content, _Answer):
        logger.warning("yes/no check gave no valid output (%s)", run.status)
        return None
    answer = run.content.answer
    return answer if answer != "other" else None
