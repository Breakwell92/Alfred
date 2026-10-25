"""Creating routines by talking to Alfred: the `routines` specialist and its tools."""

import asyncio
import json
from typing import Any

from alfred_server.agents.registry import get_registry
from alfred_server.agents.router import Router
from alfred_server.confirmations.policy import ToolPolicy, summarise_call
from alfred_server.connectors.builtin.routines import RoutinesTools, summarise_save
from alfred_server.connectors.models import ToolError
from alfred_server.connectors.registry import default_registry
from alfred_server.routines.models import RoutineStep
from tests.router_fakes import FakeBudget
from tests.routine_helpers import FakeLoader, make_service, movie_time

STEPS = [s.model_dump() for s in movie_time().steps]


def tools() -> tuple[RoutinesTools, Any]:
    service, loader, _, _ = make_service()
    return RoutinesTools(service), service


def test_save_routine_saves_and_approves_gated_steps() -> None:
    toolkit, service = tools()
    send = RoutineStep(
        connector="test_messages", tool="send_test_message", args={"recipient": "Sam", "text": "Hi"}
    ).model_dump()
    said = asyncio.run(toolkit.save_routine("movie time", ["movie time"], [*STEPS, send]))
    assert said == "Saved the movie time routine. Say 'movie time' to run it."
    saved = service.find("movie time")
    assert len(saved.steps) == 3
    # Approving the save (with every step read back) approved the step that asks first.
    assert saved.approved_tools == ["send_test_message"]


def test_save_routine_refuses_what_a_routine_cant_hold() -> None:
    toolkit, service = tools()
    unlock = {"connector": "home_assistant", "tool": "ha_unlock", "args": {"name": "front door"}}
    said = asyncio.run(toolkit.save_routine("leave", ["leaving"], [unlock]))
    assert isinstance(said, ToolError)
    assert "approval in the Alfred app" in said
    bad = asyncio.run(toolkit.save_routine("x", ["x marks"], [{"tool": "ha_turn_on"}]))
    assert isinstance(bad, ToolError) and "isn't valid" in bad
    wrong_args = [{"connector": "home_assistant", "tool": "ha_turn_on", "args": {"room": "x"}}]
    said = asyncio.run(toolkit.save_routine("y", ["y not"], wrong_args))
    assert isinstance(said, ToolError) and "no setting called room" in said
    assert service.all() == []


def test_list_and_delete_routines() -> None:
    toolkit, service = tools()
    assert asyncio.run(toolkit.list_routines()) == "There are no routines yet."
    asyncio.run(toolkit.save_routine("movie time", ["movie time"], STEPS))
    listed = asyncio.run(toolkit.list_routines())
    assert listed.startswith("movie time: say movie time; it will set the lounge lights and")
    assert asyncio.run(toolkit.delete_routine("Movie Time")) == "Deleted the movie time routine."
    assert isinstance(asyncio.run(toolkit.delete_routine("movie time")), ToolError)


def test_list_step_tools_is_json() -> None:
    toolkit, _ = tools()
    catalog = json.loads(asyncio.run(toolkit.list_step_tools()))
    assert any(t["connector"] == "home_assistant" and t["tool"] == "ha_turn_on" for t in catalog)


def test_without_a_service_every_tool_says_so() -> None:
    toolkit = RoutinesTools(None)
    for said in (
        asyncio.run(toolkit.list_step_tools()),
        asyncio.run(toolkit.save_routine("a", ["a b"], STEPS)),
        asyncio.run(toolkit.list_routines()),
        asyncio.run(toolkit.delete_routine("a")),
    ):
        assert isinstance(said, ToolError)


def test_the_confirmation_reads_every_step_back() -> None:
    policy = ToolPolicy(default_registry())
    send = {
        "connector": "test_messages",
        "tool": "send_test_message",
        "args": {"recipient": "Sam", "text": "on my way"},
        "delay_s": 10,
    }
    args = {"name": "movie time", "phrases": ["movie time", "cinema"], "steps": [*STEPS, send]}
    summary = summarise_call(policy, "save_routine", args)
    assert summary == summarise_save(policy, args)
    assert summary == (
        "Save the routine 'movie time', run by saying 'movie time' or 'cinema': set the lounge "
        "lights, turn on media_player TV and after 10 seconds, send a test message to Sam: "
        "'on my way' (it will run without asking each time)"
    )


def test_routines_specialist_gets_only_read_tools_from_other_connectors() -> None:
    loader = FakeLoader()
    service, *_ = make_service(loader)

    async def tool_loader(specialist: str) -> list[Any]:
        if specialist == "routines":
            return [RoutinesTools(service)]
        return await loader(specialist)

    router = Router(get_registry(), FakeBudget(), lambda *a, **k: None, None, None, tool_loader)  # type: ignore[arg-type]
    specialist = get_registry().get("routines")
    loaded = asyncio.run(router._connector_tools(specialist, ToolPolicy(default_registry())))
    names = {n for t in loaded for n in (*t.functions, *t.async_functions)}
    assert {"save_routine", "delete_routine", "list_step_tools", "list_routines"} <= names
    assert {"ha_get_state", "spotify_search", "spotify_my_playlists"} <= names
    # No action tools, not even ones that run without asking.
    assert not {"ha_turn_on", "ha_set_light", "spotify_play", "send_test_message"} & names
    assert loader.loaded == ["home", "media", "general"]
