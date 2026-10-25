"""Routines for tests: a "movie time" spec, an in-memory service and a fake tool loader."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from alfred_server.confirmations.models import Tier
from alfred_server.confirmations.policy import ToolPolicy
from alfred_server.connectors.builtin.test_messages import build_test_messages_toolkit
from alfred_server.connectors.models import Credentials
from alfred_server.connectors.registry import default_registry
from alfred_server.routines.models import RoutineSpec, RoutineStep
from alfred_server.routines.runner import RoutineRunner
from alfred_server.routines.service import RoutineService
from alfred_server.routines.store import RoutineStore
from tests.budget_helpers import memory_engine
from tests.ha_fakes import FakeHomeAssistant
from tests.spotify_fakes import FakeSpotify


def movie_time(**fields: Any) -> RoutineSpec:
    """Dim the lounge lights, then turn on the TV."""
    spec: dict[str, Any] = {
        "name": "movie time",
        "phrases": ["movie time"],
        "steps": [
            RoutineStep(
                connector="home_assistant",
                tool="ha_set_light",
                args={"area": "lounge", "brightness": 30},
            ),
            RoutineStep(
                connector="home_assistant",
                tool="ha_turn_on",
                args={"domain": "media_player", "name": "TV"},
            ),
        ],
    }
    spec.update(fields)
    return RoutineSpec(**spec)


class FakeLoader:
    """A `ToolLoader` over fakes: Home Assistant, Spotify and the test messages toolkit.

    Records which specialists were loaded. A specialist in `broken` raises.
    """

    def __init__(self) -> None:
        self.ha = FakeHomeAssistant()
        self.spotify = FakeSpotify()
        self.loaded: list[str] = []
        self.broken: set[str] = set()
        self.disconnected: set[str] = set()

    async def __call__(self, specialist: str) -> list[Any]:
        self.loaded.append(specialist)
        if specialist in self.broken:
            raise RuntimeError("tools wouldn't load")
        if specialist in self.disconnected:
            return []
        if specialist == "home":
            return [self.ha.tools()]
        if specialist == "media":
            return [self.spotify.tools()]
        if specialist == "general":
            return [build_test_messages_toolkit(Credentials())]
        return []


class Policies:
    """A changeable policy loader, like the confirmation service's `policy`."""

    def __init__(self, overrides: Mapping[tuple[str, str], Tier] | None = None) -> None:
        self.overrides = dict(overrides or {})
        self.down = False

    def __call__(self) -> ToolPolicy:
        if self.down:
            from alfred_server.confirmations.service import ConfirmationsUnavailableError

            raise ConfirmationsUnavailableError("Permission settings are unavailable")
        return ToolPolicy(default_registry(), self.overrides)


class NoSleep:
    def __init__(self) -> None:
        self.slept: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)


def make_service(
    loader: FakeLoader | None = None, policies: Policies | None = None
) -> tuple[RoutineService, FakeLoader, Policies, NoSleep]:
    loader = loader or FakeLoader()
    policies = policies or Policies()
    sleep = NoSleep()
    service = RoutineService(
        RoutineStore(memory_engine()),
        policies,
        loader,
        runner=RoutineRunner(loader, sleep=sleep),
    )
    return service, loader, policies, sleep
