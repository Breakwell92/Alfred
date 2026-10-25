"""Specialist registry: the agents the router can hand a request to.

A specialist is a name, a one-line description (shown to the router's classifier) and a
factory that builds its Agno agent for one request on the model the router chose. Specialist
tickets register theirs in `get_registry()`. The echo agent is `general` until a real one exists.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from agno.agent import Agent
from agno.db.base import AsyncBaseDb, BaseDb
from agno.models.base import Model

from alfred_server.agents.context import RequestContext
from alfred_server.agents.echo import build_echo_agent
from alfred_server.agents.home import HOME_AGENT_NAME, build_home_agent
from alfred_server.agents.media import MEDIA_AGENT_NAME, build_media_agent
from alfred_server.agents.routines import (
    ROUTINE_SOURCES,
    ROUTINES_AGENT_NAME,
    build_routines_agent,
)

# Handles anything no other specialist claims. Always registered.
GENERAL = "general"

# Builds the specialist's agent for one request, on the model the router picked.
AgentFactory = Callable[[RequestContext, Model, BaseDb | AsyncBaseDb], Agent]


@dataclass(frozen=True)
class Specialist:
    name: str
    description: str
    factory: AgentFactory
    # True if the agent's tools come from connectors (`Connector.specialist == name`): the
    # router loads them per request and adds them to the factory's agent before gating.
    uses_connectors: bool = False
    # Other specialists whose connector tools it also gets, only their `read` ones (by declared
    # risk, so not even tools that run without asking). For `routines`, which looks names up
    # but must never act. Its own connectors' tools are loaded in full.
    read_only_sources: tuple[str, ...] = ()


class SpecialistRegistry:
    def __init__(self) -> None:
        self._specialists: dict[str, Specialist] = {}

    def register(self, specialist: Specialist) -> None:
        if specialist.name in self._specialists:
            raise ValueError(f"specialist {specialist.name!r} is already registered")
        self._specialists[specialist.name] = specialist

    def get(self, name: str) -> Specialist:
        """The specialist called `name`, or `general` if there's none by that name."""
        return self._specialists.get(name) or self._specialists[GENERAL]

    def names(self) -> list[str]:
        return list(self._specialists)

    def describe(self) -> str:
        """One "name = description" line per specialist, for the classifier's prompt."""
        return "\n".join(f"{s.name} = {s.description}" for s in self._specialists.values())


def _general_factory(ctx: RequestContext, model: Model, db: BaseDb | AsyncBaseDb) -> Agent:
    return build_echo_agent(ctx, db=db, model=model)


def _media_factory(ctx: RequestContext, model: Model, db: BaseDb | AsyncBaseDb) -> Agent:
    return build_media_agent(ctx, db=db, model=model)


def _home_factory(ctx: RequestContext, model: Model, db: BaseDb | AsyncBaseDb) -> Agent:
    return build_home_agent(ctx, db=db, model=model)


def _routines_factory(ctx: RequestContext, model: Model, db: BaseDb | AsyncBaseDb) -> Agent:
    return build_routines_agent(ctx, db=db, model=model)


@lru_cache
def get_registry() -> SpecialistRegistry:
    """The process-wide registry with every specialist registered."""
    registry = SpecialistRegistry()
    registry.register(
        Specialist(GENERAL, "general questions, chat and anything else", _general_factory)
    )
    # Second, before media and home: `local-router` reads the list in order, and with routines
    # last it sent "something relaxing for dinner" to home and "activate movie time" here.
    # Run the full eval set after moving or rewording it.
    registry.register(
        Specialist(
            ROUTINES_AGENT_NAME,
            "setting up a new routine ('when I say X, do Y'), or listing or deleting routines",
            _routines_factory,
            uses_connectors=True,
            read_only_sources=ROUTINE_SOURCES,
        )
    )
    registry.register(
        Specialist(
            MEDIA_AGENT_NAME,
            "music: play artists, songs, playlists or music for a mood; pause, skip, volume, "
            "what's playing, speakers (Spotify)",
            _media_factory,
            uses_connectors=True,
        )
    )
    registry.register(
        Specialist(
            HOME_AGENT_NAME,
            # Kept short: `local-router` is sensitive to this wording. Longer versions sent
            # trips and dinner music here; run the full eval set after changing it.
            "lights, heating, blinds, door locks, garage door (Home Assistant)",
            _home_factory,
            uses_connectors=True,
        )
    )
    return registry
