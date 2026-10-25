"""The media specialist end to end through the router: connector tools loaded and gated."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from agno.db.sqlite import AsyncSqliteDb
from agno.models.base import Model
from agno.tools import Toolkit

from alfred_server.agents.context import RequestContext
from alfred_server.agents.media import MEDIA_AGENT_NAME
from alfred_server.agents.registry import GENERAL, get_registry
from alfred_server.agents.router import Classification, ModelChoice, Route, Router
from alfred_server.config import Settings
from alfred_server.confirmations.policy import ToolPolicy
from alfred_server.models.gateway import CallMetadata, ModelAlias, gateway_model
from evals.mocks import MockToolLoader
from tests.llm_fakes import FakeProxy, reply, tool_call_completion
from tests.router_fakes import FakeBudget, FakeGate
from tests.spotify_fakes import FakeSpotify


def ctx() -> RequestContext:
    return RequestContext.build(timezone="Europe/London", device_type="text", session_id="s1")


class Loader:
    """A `ToolLoader` handing out the fake Spotify's toolkit; records who asked."""

    def __init__(self, spotify: FakeSpotify, fail: bool = False) -> None:
        self.spotify = spotify
        self.fail = fail
        self.asked: list[str] = []

    async def __call__(self, specialist: str) -> list[Toolkit]:
        self.asked.append(specialist)
        if self.fail:
            raise RuntimeError("connector store is down")
        return [self.spotify.tools()]


class TightGate(FakeGate):
    """A gate where Dan has tightened `spotify_play` back to `write`."""

    def policy(self) -> ToolPolicy:
        return ToolPolicy(self.registry, {("spotify", "spotify_play"): "write"})


class Harness:
    def __init__(self, tmp_path: Path, *, fail: bool = False, gate: FakeGate | None = None):
        self.proxy = FakeProxy()
        self.spotify = FakeSpotify()
        self.loader = Loader(self.spotify, fail)
        self.gate = gate or FakeGate()
        db = AsyncSqliteDb(db_file=str(tmp_path / "agno.db"))
        self.router = Router(get_registry(), FakeBudget(), self.model, db, self.gate, self.loader)

    def model(self, alias: ModelAlias, metadata: CallMetadata, **kwargs: Any) -> Model:
        return gateway_model(
            alias,
            metadata=metadata,
            settings=Settings(_env_file=None, litellm_api_key="sk-test"),
            http_client=self.proxy.async_client(),
            **kwargs,
        )

    def run(self, text: str, specialist: str = MEDIA_AGENT_NAME) -> Any:
        route = Route(
            Classification(specialist=specialist),
            get_registry().get(specialist),
            ModelChoice("local-main", "test"),
        )
        return asyncio.run(self.router.run(route, text, ctx()))

    def offered_tools(self, i: int = 0) -> list[str]:
        return [t["function"]["name"] for t in self.proxy.body(i).get("tools") or []]


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def test_media_gets_spotify_tools_and_controls_run_without_asking(h: Harness) -> None:
    h.proxy.responses += [tool_call_completion("spotify_pause", {}), reply("Paused.")]

    result = h.run("Pause the music.")

    assert result.reply == "Paused."
    assert result.confirmation is None
    assert h.spotify.calls() == ["PUT /me/player/pause"]
    assert h.loader.asked == [MEDIA_AGENT_NAME]
    assert {"spotify_play", "spotify_pause", "spotify_now_playing"} <= set(h.offered_tools())
    assert h.gate.created == []


def test_a_tightened_control_asks_first(tmp_path: Path) -> None:
    h = Harness(tmp_path, gate=TightGate())
    h.proxy.responses += [tool_call_completion("spotify_play", {"query": "Fleetwood Mac"})]

    result = h.run("Play some Fleetwood Mac.")

    assert result.confirmation is not None
    assert result.confirmation.summary == "Play Fleetwood Mac on Spotify"
    assert h.spotify.calls("PUT") == []  # nothing played


def test_a_loader_failure_runs_the_agent_without_connector_tools(tmp_path: Path) -> None:
    h = Harness(tmp_path, fail=True)
    h.proxy.responses += [reply("Spotify isn't connected yet.")]

    result = h.run("Pause the music.")

    assert result.reply == "Spotify isn't connected yet."
    assert h.offered_tools() == []


def test_specialists_without_connectors_never_load_them(h: Harness) -> None:
    h.proxy.responses += [reply("Hello.")]

    h.run("Hi", specialist=GENERAL)

    assert h.loader.asked == []
    assert "spotify_pause" not in h.offered_tools()


def test_the_eval_loader_builds_media_toolkits_without_credentials() -> None:
    loaded = asyncio.run(MockToolLoader()(MEDIA_AGENT_NAME))

    assert [t.name for t in loaded] == ["spotify"]
    assert "spotify_play" in loaded[0].async_functions
    assert asyncio.run(MockToolLoader()(GENERAL)) != []  # time and test_messages
