"""The `home_assistant` connector: pinned intents, risk tiers and the MCP client."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from starlette.types import ASGIApp, Receive, Scope, Send

from alfred_server.config import Settings, get_settings
from alfred_server.confirmations.policy import ToolPolicy, summarise_call
from alfred_server.connectors.builtin.home_assistant import (
    DOOR_HINT,
    HOME_ASSISTANT_CONNECTOR,
    NOT_CONFIGURED,
    RELAY_HINT,
    SAFE_COVER_CLASSES,
    SECURE_COVER_CLASSES,
    SIGNED_OUT,
    UNREACHABLE,
    HomeAssistantError,
    HomeAssistantMcp,
    HomeAssistantTools,
    build_home_assistant_toolkit,
    describe_result,
)
from alfred_server.connectors.models import Credentials
from alfred_server.connectors.registry import default_registry
from tests.ha_fakes import FakeHomeAssistant, intent_done, result


def run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def ha() -> FakeHomeAssistant:
    return FakeHomeAssistant()


# --- Pinned intents ---------------------------------------------------------------------------


def test_lights_by_room_send_the_light_domain(ha: FakeHomeAssistant) -> None:
    reply = run(ha.tools().ha_turn_on("light", area="kitchen"))

    assert ha.calls == [("HassTurnOn", {"area": "kitchen", "domain": ["light"]})]
    assert reply == "Done: Lounge Lights."


def test_turn_off_by_floor(ha: FakeHomeAssistant) -> None:
    run(ha.tools().ha_turn_off("light", floor="downstairs"))

    assert ha.calls == [("HassTurnOff", {"floor": "downstairs", "domain": ["light"]})]


@pytest.mark.parametrize("domain", ["lock", "alarm_control_panel", "valve", "script", "garage"])
def test_ordinary_switching_never_reaches_protected_domains(
    ha: FakeHomeAssistant, domain: str
) -> None:
    tools = ha.tools()
    for tool in (tools.ha_turn_on, tools.ha_turn_off):
        reply = run(tool(domain, name="front door"))  # type: ignore[arg-type]
        assert reply  # an explanation, not an error

    assert ha.calls == []


def test_locks_point_at_the_lock_tools(ha: FakeHomeAssistant) -> None:
    assert "ha_lock" in run(ha.tools().ha_turn_on("lock", name="front door"))  # type: ignore[arg-type]


@pytest.mark.parametrize("device_class", SECURE_COVER_CLASSES)
def test_ordinary_switching_never_moves_garage_doors(
    ha: FakeHomeAssistant, device_class: str
) -> None:
    reply = run(ha.tools().ha_turn_on("cover", name="garage", device_class=device_class))  # type: ignore[arg-type]

    assert reply == DOOR_HINT
    assert ha.calls == []


@pytest.mark.parametrize("domain", ["switch", "input_boolean"])
@pytest.mark.parametrize(
    "target",
    [
        {"name": "garage door"},
        {"name": "Garage"},
        {"name": "front door relay"},
        {"name": "backdoor lock"},
        {"name": "gate opener"},
        {"name": "alarm"},
        {"area": "garage"},
        {"name": "garage_door"},
        {"name": "frontgate"},
        {"name": "Front Door Lock"},
        {"name": "doorlock relay"},
    ],
)
def test_switches_never_drive_locks_or_doors_wired_as_relays(
    ha: FakeHomeAssistant, domain: str, target: dict[str, str]
) -> None:
    # "Open the garage": ha_open_door matched no cover, so the model tries the relay instead.
    tools = ha.tools()
    for tool in (tools.ha_turn_on, tools.ha_turn_off):
        assert run(tool(domain, **target)) == RELAY_HINT  # type: ignore[arg-type]

    assert ha.calls == []


@pytest.mark.parametrize(
    "target",
    [
        {"name": "outdoor lights"},
        {"name": "indoor fan"},
        {"name": "clock"},
        {"area": "Outdoor"},
        {"name": "doorbell chime"},
        {"name": "network gateway"},
        {"name": "kettle"},
    ],
)
def test_switches_with_harmless_names_are_not_refused(
    ha: FakeHomeAssistant, target: dict[str, str]
) -> None:
    reply = run(ha.tools().ha_turn_on("switch", **target))  # type: ignore[arg-type]

    assert reply != RELAY_HINT
    assert len(ha.calls) == 1


def test_ordinary_switches_still_work(ha: FakeHomeAssistant) -> None:
    run(ha.tools().ha_turn_on("switch", name="kettle", area="kitchen"))

    assert ha.calls == [("HassTurnOn", {"name": "kettle", "area": "kitchen", "domain": ["switch"]})]


def test_covers_without_a_class_are_limited_to_blinds_and_curtains(ha: FakeHomeAssistant) -> None:
    # "Open the garage door" sent to ha_turn_on must not match the garage door by name.
    run(ha.tools().ha_turn_on("cover", name="garage door"))

    (call,) = ha.calls
    assert call == (
        "HassTurnOn",
        {"name": "garage door", "domain": ["cover"], "device_class": list(SAFE_COVER_CLASSES)},
    )
    assert not set(call[1]["device_class"]) & set(SECURE_COVER_CLASSES)


def test_a_named_blind_class_is_passed_on(ha: FakeHomeAssistant) -> None:
    run(ha.tools().ha_turn_off("cover", area="bedroom", device_class="blind"))

    assert ha.calls[0][1]["device_class"] == ["blind"]


def test_switching_needs_a_target(ha: FakeHomeAssistant) -> None:
    assert run(ha.tools().ha_turn_on("light")) == "Which device or room?"
    assert ha.calls == []


def test_set_light_brightness_is_clamped(ha: FakeHomeAssistant) -> None:
    tools = ha.tools()
    run(tools.ha_set_light(area="lounge", brightness=40))
    run(tools.ha_set_light(name="lamp", brightness=140, color="red"))

    assert ha.calls == [
        ("HassLightSet", {"area": "lounge", "brightness": 40}),
        ("HassLightSet", {"name": "lamp", "brightness": 100, "color": "red"}),
    ]


def test_set_light_needs_something_to_set(ha: FakeHomeAssistant) -> None:
    assert run(ha.tools().ha_set_light(area="lounge")) == "What brightness or colour?"
    assert ha.calls == []


def test_heating_set_point(ha: FakeHomeAssistant) -> None:
    run(ha.tools().ha_set_temperature(21))

    assert ha.calls == [("HassClimateSetTemperature", {"temperature": 21.0})]


@pytest.mark.parametrize("temperature", [2, 35])
def test_heating_refuses_silly_temperatures(ha: FakeHomeAssistant, temperature: float) -> None:
    assert "outside" in run(ha.tools().ha_set_temperature(temperature))
    assert ha.calls == []


def test_lock_tools_pin_the_lock_domain(ha: FakeHomeAssistant) -> None:
    tools = ha.tools()
    run(tools.ha_lock("front door"))
    run(tools.ha_unlock("back door", area="kitchen"))

    assert ha.calls == [
        ("HassTurnOn", {"name": "front door", "domain": ["lock"]}),
        ("HassTurnOff", {"name": "back door", "area": "kitchen", "domain": ["lock"]}),
    ]


def test_door_tools_pin_garage_classes(ha: FakeHomeAssistant) -> None:
    tools = ha.tools()
    run(tools.ha_open_door("garage door"))
    run(tools.ha_close_door("gate"))

    door = {"domain": ["cover"], "device_class": list(SECURE_COVER_CLASSES)}
    assert ha.calls == [
        ("HassTurnOn", {"name": "garage door", **door}),
        ("HassTurnOff", {"name": "gate", **door}),
    ]


def test_get_state_returns_the_live_context(ha: FakeHomeAssistant) -> None:
    reply = run(ha.tools().ha_get_state())

    assert ha.calls == [("GetLiveContext", {})]
    assert reply.startswith("Live Context:")
    assert "Back Door" in reply


# --- Results and errors -----------------------------------------------------------------------


def test_describe_result_lists_successes_and_failures() -> None:
    body = {
        "response_type": "action_done",
        "data": {"success": [{"name": "Lamp"}], "failed": [{"name": "Strip"}]},
    }

    assert describe_result(result(json.dumps(body))) == "Done: Lamp. Failed: Strip."


def test_describe_result_reports_ha_errors() -> None:
    error = {"response_type": "error", "speech": {"plain": {"speech": "No device named attic"}}}

    assert describe_result(result(json.dumps(error))) == (
        "Home Assistant couldn't do that: No device named attic."
    )
    assert describe_result(result("MatchFailedError: no lounge", error=True)).startswith(
        "Home Assistant couldn't do that: MatchFailedError"
    )


def test_describe_result_passes_plain_text_through() -> None:
    assert describe_result(result("Turned on the lights")) == "Turned on the lights"


def test_client_errors_are_said_not_raised(ha: FakeHomeAssistant) -> None:
    ha.fail = HomeAssistantError(UNREACHABLE)
    tools = ha.tools()

    assert run(tools.ha_turn_on("light", area="lounge")) == UNREACHABLE
    assert run(tools.ha_get_state()) == UNREACHABLE


def test_unconfigured_toolkit_says_so() -> None:
    tools = HomeAssistantTools(None)

    assert run(tools.ha_lock("front door")) == NOT_CONFIGURED
    assert run(tools.ha_get_state()) == NOT_CONFIGURED


def test_factory_uses_the_url_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    assert build_home_assistant_toolkit(Credentials(api_key="t")).client is None  # type: ignore[attr-defined]

    monkeypatch.setenv("ALFRED_HOME_ASSISTANT_URL", "http://ha.test:8123/")
    get_settings.cache_clear()
    toolkit = build_home_assistant_toolkit(Credentials(api_key="t"))

    assert isinstance(toolkit, HomeAssistantTools)
    assert isinstance(toolkit.client, HomeAssistantMcp)
    assert toolkit.client.url == "http://ha.test:8123/api/mcp"


def test_settings_default_timeout() -> None:
    assert Settings(_env_file=None).home_assistant_timeout_s == 10.0


# --- Connector definition and tiers -----------------------------------------------------------


def test_every_toolkit_function_is_declared() -> None:
    toolkit = HomeAssistantTools(None)

    assert set(toolkit.async_functions) == set(HOME_ASSISTANT_CONNECTOR.tool_names)


def test_connector_definition() -> None:
    registry = default_registry()
    connector = registry.get("home_assistant")

    assert connector is HOME_ASSISTANT_CONNECTOR
    assert connector.auth_type == "api_key"
    assert connector.specialist == "home"
    state = registry.tool_meta("ha_get_state")
    assert state is not None and state[1].privacy == "local_only"


def test_ordinary_control_runs_and_locks_and_doors_need_the_app() -> None:
    policy = ToolPolicy(default_registry())

    for name in ("ha_get_state", "ha_turn_on", "ha_turn_off", "ha_set_light"):
        assert policy.tier(name) == "read", name
    assert policy.tier("ha_set_temperature") == "read"
    for name in ("ha_lock", "ha_unlock", "ha_open_door", "ha_close_door"):
        assert policy.tier(name) == "irreversible", name
        meta = default_registry().tool_meta(name)
        assert meta is not None and meta[1].default_tier is None


def test_dan_can_tighten_ordinary_control() -> None:
    policy = ToolPolicy(default_registry(), {("home_assistant", "ha_turn_on"): "write"})

    assert policy.gated("ha_turn_on")


def test_confirmation_summaries() -> None:
    policy = ToolPolicy(default_registry())

    assert summarise_call(policy, "ha_lock", {"name": "front door"}) == "Lock the front door"
    assert summarise_call(policy, "ha_open_door", {"name": "garage door"}) == (
        "Open the garage door"
    )


# --- The real MCP client against an MCP server over streamable HTTP ---------------------------

TOKEN = "ha-test-token"


def _ha_server(calls: list[tuple[str, dict[str, Any]]]) -> MCPServer:
    """Two of Home Assistant's MCP tools, recording their calls."""
    server = MCPServer("fake-home-assistant")

    @server.tool(name="HassTurnOn")
    def hass_turn_on(
        name: str | None = None, area: str | None = None, domain: list[str] | None = None
    ) -> str:
        """Turn on."""
        calls.append(("HassTurnOn", {k: v for k, v in locals().items() if v}))
        target = name or area or "?"
        success = [{"name": f"{target} ({','.join(domain or [])})"}]
        return json.dumps({"response_type": "action_done", "data": {"success": success}})

    @server.tool(name="GetLiveContext")
    def get_live_context() -> str:
        """Live context."""
        calls.append(("GetLiveContext", {}))
        return json.dumps({"success": True, "result": "Live Context: Back Door is locked"})

    return server


class _RequireToken:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = dict(scope.get("headers") or []).get(b"authorization")
        if scope["type"] == "http" and token != f"Bearer {TOKEN}".encode():
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await self.app(scope, receive, send)


class _Server:
    """A fresh fake HA MCP server on `port`, run by uvicorn in a thread."""

    def __init__(self, port: int) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # Event-stream responses: plain JSON responses from a local uvicorn stall on Dan's
        # machine (re-chunked by its antivirus' web filter), so the test doesn't rely on them.
        app = _ha_server(self.calls).streamable_http_app(
            streamable_http_path="/api/mcp", stateless_http=True
        )
        config = uvicorn.Config(
            _RequireToken(app), port=port, log_level="warning", timeout_graceful_shutdown=1
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> _Server:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("fake HA server didn't start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def port() -> Iterator[int]:
    yield _free_port()


def _client(port: int, token: str = TOKEN) -> HomeAssistantMcp:
    return HomeAssistantMcp(f"http://127.0.0.1:{port}", token, timeout_s=5, retry_delay_s=0.05)


def test_mcp_client_calls_ha_with_the_token(port: int) -> None:
    tools = HomeAssistantTools(_client(port))
    with _Server(port) as server:
        reply = run(tools.ha_lock("front door"))
        state = run(tools.ha_get_state())

    assert server.calls == [
        ("HassTurnOn", {"name": "front door", "domain": ["lock"]}),
        ("GetLiveContext", {}),
    ]
    assert reply == "Done: front door (lock)."
    assert state == "Live Context: Back Door is locked"


def test_mcp_client_reports_a_rejected_token(port: int) -> None:
    with _Server(port):
        reply = run(HomeAssistantTools(_client(port, "wrong")).ha_get_state())

    assert reply == SIGNED_OUT


def test_mcp_client_recovers_when_ha_restarts(port: int) -> None:
    tools = HomeAssistantTools(_client(port))
    with _Server(port):
        assert run(tools.ha_turn_on("light", area="lounge")) == "Done: lounge (light)."

    assert run(tools.ha_turn_on("light", area="lounge")) == UNREACHABLE  # HA is down

    with _Server(port):  # back up: the same toolkit works again, nothing to reconnect
        assert run(tools.ha_turn_on("light", area="lounge")) == "Done: lounge (light)."


def test_mcp_client_retries_once() -> None:
    class Flaky(HomeAssistantMcp):
        attempts = 0

        async def _call_once(self, tool: str, arguments: dict[str, Any]) -> CallToolResult:
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("HA restarting")
            return intent_done("Lamp")

    client = Flaky("http://ha.test", TOKEN, retry_delay_s=0)

    assert describe_result(run(client.call("HassTurnOn", {}))) == "Done: Lamp."
    assert client.attempts == 2
