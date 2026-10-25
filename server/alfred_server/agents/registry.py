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


@lru_cache
def get_registry() -> SpecialistRegistry:
    """The process-wide registry with every specialist registered."""
    registry = SpecialistRegistry()
    registry.register(
        Specialist(GENERAL, "general questions, chat and anything else", _general_factory)
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
