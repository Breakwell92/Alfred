"""`home_assistant` connector: lights, heating, scenes, locks and garage doors (STU-62).

Alfred talks to Home Assistant's MCP server (`<ALFRED_HOME_ASSISTANT_URL>/api/mcp`, streamable
HTTP, a long-lived access token as the bearer). HA's MCP tools are its generic Assist intents:
`HassTurnOn` switches a light on, but also locks a lock and opens a garage door. Confirmation
tiers are per tool, so the agent never sees HA's tools. It gets the `ha_*` tools below instead,
each of which calls one intent with the domain (and cover device class) pinned here. HA only
matches entities of the domain and device class it's sent, so a light tool can't reach a lock
whatever name the model passes, and locks and garage doors get their own `irreversible` tools.

Every call opens a fresh MCP session, so nothing goes stale when HA restarts. Every tool answers
with a short sentence the home agent can say, errors included, and never raises. Setup:
docs/connectors/home-assistant.md. Why: docs/decisions/0019-home-assistant.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Literal, Protocol, get_args

import httpx2
from agno.tools import Toolkit
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.types import CallToolResult, TextContent

from alfred_server.config import get_settings
from alfred_server.connectors.models import AgnoToolkitSource, Connector, Credentials, ToolMeta

logger = logging.getLogger(__name__)

MCP_PATH = "/api/mcp"
# One retry after a connection failure: HA restarting, or a dropped connection.
RETRY_DELAY_S = 1.0
# Longest HA reply passed back to the model (the live context of a big house is long).
MAX_RESULT_CHARS = 6000

NOT_CONFIGURED = "Home Assistant isn't set up: ALFRED_HOME_ASSISTANT_URL is missing on the server."
UNREACHABLE = "Home Assistant isn't reachable right now."
SIGNED_OUT = (
    "Home Assistant refused Alfred's access token. Set a new one in Alfred's connector settings."
)

# Domains `ha_turn_on` / `ha_turn_off` may act on. Locks, alarm panels, valves, scripts (which
# can do anything, unlocking included) and the rest are left out on purpose: see the docstring.
SafeDomain = Literal[
    "light", "switch", "fan", "media_player", "scene", "input_boolean", "climate", "cover"
]
SAFE_DOMAINS: frozenset[str] = frozenset(get_args(SafeDomain))
# Covers (`cover` domain) ordinary control may move. Garage doors, gates and doors are
# `ha_open_door` / `ha_close_door`, which need approval in the app.
SafeCoverClass = Literal["blind", "curtain", "shade", "shutter", "awning", "window"]
SAFE_COVER_CLASSES: tuple[str, ...] = get_args(SafeCoverClass)
SECURE_COVER_CLASSES: tuple[str, ...] = ("garage", "gate", "door")
SECURITY_HINT = {
    "lock": "Locks use ha_lock and ha_unlock, which need approval in the Alfred app.",
    "alarm_control_panel": "I can't arm or disarm the alarm.",
}
DOOR_HINT = (
    "Garage doors, gates and doors use ha_open_door and ha_close_door, which need approval in "
    "the Alfred app."
)
# Garage openers and locks are often wired as relays (`switch.garage_door`), which a switch call
# would run without approval. Switch-like calls naming one of these are refused (a backstop:
# the setup doc also says never to expose such switches).
RELAY_DOMAINS = frozenset({"switch", "input_boolean"})
# Substrings, not words, so "backdoor" and "garagedoor" count too (and, harmlessly, "clock").
SECURE_WORDS = re.compile(r"lock|door|gate|garage|alarm", re.I)
RELAY_HINT = (
    "That sounds like a lock, door, gate or alarm. I won't switch it directly: ha_lock, "
    "ha_unlock, ha_open_door and ha_close_door handle those, with approval in the Alfred app."
)
MIN_TEMPERATURE_C = 5.0
MAX_TEMPERATURE_C = 30.0


class HomeAssistantError(Exception):
    """A failure talking to HA, with the sentence to say about it."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class HomeAssistantClient(Protocol):
    async def call(self, tool: str, arguments: dict[str, Any]) -> CallToolResult: ...


class HomeAssistantMcp:
    """Calls one HA MCP tool per session, on a fresh HTTP client each time."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = 10.0,
        retry_delay_s: float = RETRY_DELAY_S,
    ) -> None:
        self.url = base_url.rstrip("/") + MCP_PATH
        self._token = token
        self._timeout_s = timeout_s
        self._retry_delay_s = retry_delay_s

    def _client(self, refused: list[int]) -> httpx2.AsyncClient:
        client = create_mcp_http_client(
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=httpx2.Timeout(self._timeout_s),
        )

        # The MCP client turns any HTTP error into a bare JSON-RPC error, so a refused token
        # is noted here, where the status is still known.
        async def note_refusal(response: httpx2.Response) -> None:
            if response.status_code in (401, 403):
                refused.append(response.status_code)

        client.event_hooks["response"] = [note_refusal]
        return client

    async def _call_once(self, tool: str, arguments: dict[str, Any]) -> CallToolResult:
        refused: list[int] = []
        try:
            async with (
                self._client(refused) as http,
                streamable_http_client(self.url, http_client=http) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await asyncio.wait_for(
                    session.call_tool(tool, arguments), timeout=self._timeout_s
                )
        except Exception as exc:
            if refused:
                raise HomeAssistantError(SIGNED_OUT) from exc
            raise
        if not isinstance(result, CallToolResult):
            raise HomeAssistantError("Home Assistant gave an answer I didn't understand.")
        return result

    async def call(self, tool: str, arguments: dict[str, Any]) -> CallToolResult:
        """HA's result for one tool call. Raises `HomeAssistantError`."""
        for attempt in (1, 2):
            try:
                return await self._call_once(tool, arguments)
            except HomeAssistantError:
                raise
            except Exception as exc:  # transport errors arrive wrapped in exception groups
                logger.warning("home assistant %s failed (attempt %d): %r", tool, attempt, exc)
                if attempt == 2:
                    raise HomeAssistantError(UNREACHABLE) from exc
                await asyncio.sleep(self._retry_delay_s)
        raise AssertionError("unreachable")


def _text(result: CallToolResult) -> str:
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent)).strip()


def _names(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(i.get("name") or i.get("id")) for i in items if isinstance(i, dict)]


def describe_result(result: CallToolResult) -> str:
    """A short sentence about an intent call's result (HA's intent response as JSON)."""
    text = _text(result)
    if result.is_error:
        return f"Home Assistant couldn't do that: {text[:300] or 'no reason given'}."
    try:
        body = json.loads(text)
    except ValueError:
        return text[:MAX_RESULT_CHARS] or "Done."
    if not isinstance(body, dict):
        return text[:MAX_RESULT_CHARS]
    speech = ((body.get("speech") or {}).get("plain") or {}).get("speech")
    if body.get("response_type") == "error":
        return f"Home Assistant couldn't do that: {speech or 'no reason given'}."
    data = body.get("data") or {}
    done, failed = _names(data.get("success")), _names(data.get("failed"))
    parts = []
    if done:
        parts.append("Done: " + ", ".join(done) + ".")
    if failed:
        parts.append("Failed: " + ", ".join(failed) + ".")
    if parts:
        return " ".join(parts)
    return str(speech) if speech else "Done."


def _targets(name: str | None, area: str | None, floor: str | None) -> dict[str, Any]:
    return {k: v for k, v in {"name": name, "area": area, "floor": floor}.items() if v}


class HomeAssistantTools(Toolkit):
    """The `ha_*` tools. `client` is None when HA isn't configured: every tool says so."""

    def __init__(self, client: HomeAssistantClient | None) -> None:
        self.client = client
        super().__init__(
            name="home_assistant",
            tools=[
                self.ha_get_state,
                self.ha_turn_on,
                self.ha_turn_off,
                self.ha_set_light,
                self.ha_set_temperature,
                self.ha_lock,
                self.ha_unlock,
                self.ha_open_door,
                self.ha_close_door,
            ],
        )

    async def _intent(self, tool: str, arguments: dict[str, Any]) -> str:
        if self.client is None:
            return NOT_CONFIGURED
        try:
            return describe_result(await self.client.call(tool, arguments))
        except HomeAssistantError as e:
            return e.message

    async def _switch(
        self,
        intent: str,
        domain: str,
        name: str | None,
        area: str | None,
        floor: str | None,
        device_class: str | None,
    ) -> str:
        if domain in SECURITY_HINT:
            return SECURITY_HINT[domain]
        if domain not in SAFE_DOMAINS:
            return f"I can't control {domain} devices."
        args = _targets(name, area, floor)
        if not args:
            return "Which device or room?"
        if domain in RELAY_DOMAINS and any(SECURE_WORDS.search(v) for v in args.values()):
            return RELAY_HINT
        args["domain"] = [domain]
        if domain == "cover":
            if device_class in SECURE_COVER_CLASSES:
                return DOOR_HINT
            if device_class is not None and device_class not in SAFE_COVER_CLASSES:
                return f"I can't control {device_class} covers."
            # HA matches only covers of these classes: never a garage door by its name.
            args["device_class"] = [device_class] if device_class else list(SAFE_COVER_CLASSES)
        return await self._intent(intent, args)

    async def ha_get_state(self) -> str:
        """The current state of every device Home Assistant exposes: names, rooms (areas) and
        states, e.g. which lights are on, temperatures, whether doors are locked.

        Returns:
            An overview of the home's devices and their states.
        """
        if self.client is None:
            return NOT_CONFIGURED
        try:
            result = await self.client.call("GetLiveContext", {})
        except HomeAssistantError as e:
            return e.message
        text = _text(result)
        if result.is_error:
            return f"Home Assistant couldn't read the devices: {text[:300]}."
        try:
            body = json.loads(text)
            if isinstance(body, dict) and isinstance(body.get("result"), str):
                text = body["result"]
        except ValueError:
            pass
        return text[:MAX_RESULT_CHARS] or "Home Assistant exposes no devices."

    async def ha_turn_on(
        self,
        domain: SafeDomain,
        name: str | None = None,
        area: str | None = None,
        floor: str | None = None,
        device_class: SafeCoverClass | None = None,
    ) -> str:
        """Turn on (or open, or activate) devices: lights, switches, fans, scenes, blinds and
        curtains, heating.

        Args:
            domain: The kind of device: "light", "switch", "fan", "media_player", "scene",
                "input_boolean", "climate" or "cover" (blinds, curtains, shutters).
            name: The device or scene name as Dan said it, e.g. "lamp" or "movie time".
                Leave out to act on every device of this kind in the area or floor.
            area: The room, e.g. "lounge" or "kitchen".
            floor: The floor, e.g. "downstairs".
            device_class: For covers only: "blind", "curtain", "shade", "shutter", "awning" or
                "window".

        Returns:
            What was turned on.
        """
        return await self._switch("HassTurnOn", domain, name, area, floor, device_class)

    async def ha_turn_off(
        self,
        domain: SafeDomain,
        name: str | None = None,
        area: str | None = None,
        floor: str | None = None,
        device_class: SafeCoverClass | None = None,
    ) -> str:
        """Turn off (or close) devices: lights, switches, fans, media players, blinds and
        curtains, heating.

        Args:
            domain: The kind of device: "light", "switch", "fan", "media_player",
                "input_boolean", "climate" or "cover" (blinds, curtains, shutters).
            name: The device name as Dan said it. Leave out to act on every device of this
                kind in the area or floor.
            area: The room, e.g. "bedroom".
            floor: The floor, e.g. "downstairs".
            device_class: For covers only: "blind", "curtain", "shade", "shutter", "awning" or
                "window".

        Returns:
            What was turned off.
        """
        return await self._switch("HassTurnOff", domain, name, area, floor, device_class)

    async def ha_set_light(
        self,
        name: str | None = None,
        area: str | None = None,
        floor: str | None = None,
        brightness: int | None = None,
        color: str | None = None,
    ) -> str:
        """Set lights' brightness or colour (this also turns them on).

        Args:
            name: The light as Dan said it; leave out for every light in the area or floor.
            area: The room, e.g. "lounge".
            floor: The floor, e.g. "upstairs".
            brightness: Brightness from 0 to 100 percent.
            color: A colour name, e.g. "red" or "warm white".

        Returns:
            Which lights changed.
        """
        args = _targets(name, area, floor)
        if not args:
            return "Which lights?"
        if brightness is None and not color:
            return "What brightness or colour?"
        if brightness is not None:
            args["brightness"] = max(0, min(100, int(brightness)))
        if color:
            args["color"] = color
        return await self._intent("HassLightSet", args)

    async def ha_set_temperature(
        self, temperature: float, name: str | None = None, area: str | None = None
    ) -> str:
        """Set the heating (thermostat) to a temperature in degrees Celsius.

        Args:
            temperature: The target temperature in °C, e.g. 21.
            name: The thermostat's name, if Dan named one; usually leave out.
            area: The room, if Dan named one.

        Returns:
            The new set point.
        """
        temperature = float(temperature)
        if not MIN_TEMPERATURE_C <= temperature <= MAX_TEMPERATURE_C:
            return (
                f"{temperature:g} degrees is outside what I'll set "
                f"({MIN_TEMPERATURE_C:g} to {MAX_TEMPERATURE_C:g})."
            )
        args: dict[str, Any] = {"temperature": temperature, **_targets(name, area, None)}
        return await self._intent("HassClimateSetTemperature", args)

    async def ha_lock(self, name: str, area: str | None = None) -> str:
        """Lock a door lock. Needs Dan's approval in the Alfred app.

        Args:
            name: The lock as Dan said it, e.g. "front door".
            area: The room, if Dan said one.

        Returns:
            Whether it locked.
        """
        return await self._intent("HassTurnOn", {**_targets(name, area, None), "domain": ["lock"]})

    async def ha_unlock(self, name: str, area: str | None = None) -> str:
        """Unlock a door lock. Needs Dan's approval in the Alfred app.

        Args:
            name: The lock as Dan said it, e.g. "back door".
            area: The room, if Dan said one.

        Returns:
            Whether it unlocked.
        """
        args = {**_targets(name, area, None), "domain": ["lock"]}
        return await self._intent("HassTurnOff", args)

    async def ha_open_door(self, name: str, area: str | None = None) -> str:
        """Open a garage door, gate or motorised door. Needs Dan's approval in the Alfred app.

        Args:
            name: The door as Dan said it, e.g. "garage door".
            area: The room, if Dan said one.

        Returns:
            Whether it opened.
        """
        return await self._intent("HassTurnOn", self._door(name, area))

    async def ha_close_door(self, name: str, area: str | None = None) -> str:
        """Close a garage door, gate or motorised door. Needs Dan's approval in the Alfred app.

        Args:
            name: The door as Dan said it, e.g. "garage door".
            area: The room, if Dan said one.

        Returns:
            Whether it closed.
        """
        return await self._intent("HassTurnOff", self._door(name, area))

    @staticmethod
    def _door(name: str, area: str | None) -> dict[str, Any]:
        return {
            **_targets(name, area, None),
            "domain": ["cover"],
            "device_class": list(SECURE_COVER_CLASSES),
        }


def build_home_assistant_toolkit(creds: Credentials) -> Toolkit:
    settings = get_settings()
    if not settings.home_assistant_url:
        return HomeAssistantTools(None)
    client = HomeAssistantMcp(
        settings.home_assistant_url,
        creds.api_key or "",
        timeout_s=settings.home_assistant_timeout_s,
    )
    return HomeAssistantTools(client)


def _control(name: str, description: str, summary: str) -> ToolMeta:
    # Ordinary device control (lights, heating, blinds, scenes): honestly `write`, but it runs
    # without asking, as the ticket says (STU-62, like media in ADR 0014). Dan can tighten it.
    return ToolMeta(
        name=name,
        description=description,
        risk="write",
        default_tier="read",
        confirm_summary=summary,
    )


def _secure(name: str, description: str, summary: str) -> ToolMeta:
    # Locks and garage doors: only an approval in the Alfred app runs them (ADR 0013, 0016).
    return ToolMeta(
        name=name, description=description, risk="irreversible", confirm_summary=summary
    )


HOME_ASSISTANT_CONNECTOR = Connector(
    id="home_assistant",
    name="Home Assistant",
    description="Lights, heating, blinds, scenes, locks and garage doors via Home Assistant.",
    auth_type="api_key",
    tool_source=AgnoToolkitSource(build_home_assistant_toolkit),
    tools=(
        # Device states can say who's home (presence sensors, doors): keep them local.
        ToolMeta(
            name="ha_get_state",
            description="States of the home's devices.",
            risk="read",
            privacy="local_only",
        ),
        _control("ha_turn_on", "Turn devices on or activate a scene.", "Turn on {domain} {name}"),
        _control("ha_turn_off", "Turn devices off.", "Turn off {domain} {name}"),
        _control("ha_set_light", "Set light brightness or colour.", "Set the {area} lights"),
        _control(
            "ha_set_temperature", "Set the heating temperature.", "Set the heating to {temperature}"
        ),
        _secure("ha_lock", "Lock a door.", "Lock the {name}"),
        _secure("ha_unlock", "Unlock a door.", "Unlock the {name}"),
        _secure("ha_open_door", "Open a garage door or gate.", "Open the {name}"),
        _secure("ha_close_door", "Close a garage door or gate.", "Close the {name}"),
    ),
    specialist="home",
)
