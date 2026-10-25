"""The home specialist end to end through the router: HA tools loaded and gated by risk."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from agno.db.sqlite import AsyncSqliteDb
from agno.models.base import Model
from agno.tools import Toolkit

from alfred_server.agents.context import RequestContext
from alfred_server.agents.home import HOME_AGENT_NAME
from alfred_server.agents.registry import get_registry
from alfred_server.agents.router import Classification, ModelChoice, Route, Router
from alfred_server.config import Settings
from alfred_server.models.gateway import CallMetadata, ModelAlias, gateway_model
from evals.mocks import MockToolLoader
from tests.ha_fakes import FakeHomeAssistant, intent_done
from tests.llm_fakes import FakeProxy, reply, tool_call_completion
from tests.router_fakes import FakeBudget, FakeGate


def ctx(**fields: Any) -> RequestContext:
    return RequestContext.build(
        timezone="Europe/London", device_type="text", session_id="s1", **fields
    )


class Loader:
    """A `ToolLoader` handing out the fake Home Assistant's toolkit."""

    def __init__(self, ha: FakeHomeAssistant) -> None:
        self.ha = ha
        self.asked: list[str] = []

    async def __call__(self, specialist: str) -> list[Toolkit]:
        self.asked.append(specialist)
        return [self.ha.tools()]


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.proxy = FakeProxy()
        self.ha = FakeHomeAssistant({"HassLightSet": intent_done("Lounge Lights")})
        self.loader = Loader(self.ha)
        self.gate = FakeGate()
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

    def run(self, text: str, **fields: Any) -> Any:
        route = Route(
            Classification(specialist=HOME_AGENT_NAME),
            get_registry().get(HOME_AGENT_NAME),
            ModelChoice("local-main", "test"),
        )
        return asyncio.run(self.router.run(route, text, ctx(**fields)))

    def offered_tools(self, i: int = 0) -> list[str]:
        return [t["function"]["name"] for t in self.proxy.body(i).get("tools") or []]

    def system_prompt(self, i: int = 0) -> str:
        return str(self.proxy.body(i)["messages"][0]["content"])


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def test_home_is_registered_with_connector_tools() -> None:
    home = get_registry().get(HOME_AGENT_NAME)

    assert home.name == HOME_AGENT_NAME
    assert home.uses_connectors


def test_lights_run_without_asking(h: Harness) -> None:
    h.proxy.responses += [
        tool_call_completion("ha_set_light", {"area": "lounge", "brightness": 40}),
        reply("The lounge lights are at 40 percent."),
    ]

    result = h.run("Turn the lounge lights to 40%.")

    assert result.reply == "The lounge lights are at 40 percent."
    assert result.confirmation is None
    assert h.ha.calls == [("HassLightSet", {"area": "lounge", "brightness": 40})]
    assert h.loader.asked == [HOME_AGENT_NAME]
    assert {"ha_get_state", "ha_set_light", "ha_lock"} <= set(h.offered_tools())


def test_heating_runs_without_asking(h: Harness) -> None:
    h.proxy.responses += [
        tool_call_completion("ha_set_temperature", {"temperature": 21}),
        reply("Heating set to 21."),
    ]

    result = h.run("Turn the heating up to 21.")

    assert result.confirmation is None
    assert h.ha.calls == [("HassClimateSetTemperature", {"temperature": 21.0})]


def test_state_questions_read_live_context(h: Harness) -> None:
    h.proxy.responses += [
        tool_call_completion("ha_get_state", {}),
        reply("Yes, the back door is locked."),
    ]

    result = h.run("Is the back door locked?")

    assert result.reply == "Yes, the back door is locked."
    assert h.ha.calls == [("GetLiveContext", {})]
    tool_message = h.proxy.body(1)["messages"][-1]
    assert "Back Door" in str(tool_message["content"])


def test_locking_waits_for_an_app_approval(h: Harness) -> None:
    h.proxy.responses += [tool_call_completion("ha_lock", {"name": "front door"})]

    result = h.run("Lock the front door.")

    assert result.confirmation is not None
    assert result.confirmation.tier == "irreversible"
    assert result.confirmation.summary == "Lock the front door"
    assert result.confirmation.tools == ["ha_lock"]
    assert h.ha.calls == []  # nothing locked yet


def test_unlocking_and_garage_doors_wait_too(tmp_path: Path) -> None:
    for tool, args in (("ha_unlock", {"name": "back door"}), ("ha_open_door", {"name": "garage"})):
        h = Harness(tmp_path / tool)
        h.proxy.responses += [tool_call_completion(tool, args)]

        result = h.run("Do it.")

        assert result.confirmation is not None and result.confirmation.tier == "irreversible"
        assert h.ha.calls == []


def test_active_room_reaches_the_agent(h: Harness) -> None:
    h.proxy.responses += [reply("Which room?")]

    h.run("Lights off.", active_room="bedroom")

    assert "active_room: bedroom" in h.system_prompt()


def test_the_eval_loader_builds_the_home_toolkit_without_credentials() -> None:
    loaded = asyncio.run(MockToolLoader()(HOME_AGENT_NAME))

    assert [t.name for t in loaded] == ["home_assistant"]
    assert "ha_lock" in loaded[0].async_functions
