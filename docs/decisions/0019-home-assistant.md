# 0019: Home Assistant connector and the Home specialist

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-62

## Context

Home Assistant (HA) is the hub for every physical device in the house: lights, heating, plugs, blinds, sensors, locks and the garage door. Alfred should control them through HA's MCP server (the **Model Context Protocol** integration, `<HA>/api/mcp`, streamable HTTP, a long-lived access token as the bearer) rather than integrating each brand.

HA's MCP tools are its generic Assist intents. `HassTurnOn` and `HassTurnOff` switch a light, but also lock or unlock a lock and open or close a garage door. Which entity they hit depends on the `name`, `area`, `domain` and `device_class` arguments. Confirmation tiers (ADR 0013) are per tool name, because Agno's `requires_confirmation` is per function. Exposing HA's tools directly (`McpHttpSource`) would leave two bad choices:

- gate `HassTurnOn` as `irreversible`, so every light needs an approval in the app (unusable); or
- leave it ungated, so "turn on the front door" unlocks it (unsafe, and CLAUDE.md forbids weakening safety to make a feature work).

## Decision

**Alfred's own toolkit over HA's MCP server.** The ticket suggested Agno's MCP tools; this deviates. `connectors/builtin/home_assistant.py` has a `HomeAssistantTools` Agno toolkit (an `AgnoToolkitSource`, like Spotify). Each `ha_*` tool calls one HA intent with the **domain, and for covers the device class, pinned by our code**. HA only matches entities of the domain and device classes it's sent. So whatever name the model passes, a light tool can't reach a lock, and each tool's risk is static and honest.

| Tool | HA intent | Tier |
| --- | --- | --- |
| `ha_get_state` | `GetLiveContext` | `read`, privacy `local_only` (states can reveal who's home) |
| `ha_turn_on` / `ha_turn_off` | `HassTurnOn` / `HassTurnOff`, `domain` ∈ light, switch, fan, media_player, scene, input_boolean, climate, cover | `write`, default tier `read` |
| `ha_set_light` | `HassLightSet` (brightness 0–100, colour) | `write`, default tier `read` |
| `ha_set_temperature` | `HassClimateSetTemperature` (5–30 °C) | `write`, default tier `read` |
| `ha_lock` / `ha_unlock` | `HassTurnOn` / `HassTurnOff`, domain `lock` | `irreversible` |
| `ha_open_door` / `ha_close_door` | `HassTurnOn` / `HassTurnOff`, domain `cover`, device class garage, gate, door | `irreversible` |

- **Covers.** `ha_turn_on`/`off` for `cover` always send a device class: the one the model named (blind, curtain, shade, shutter, awning or window) or, if it named none, all of those. A garage door's class is never among them, so "open the garage door" sent to `ha_turn_on` matches nothing. The cost: a cover with no device class set in HA can't be moved at all. Set one in HA.
- **Refused outright:** locks and alarm panels through `ha_turn_on`/`off` (the reply points at the lock tools), and any domain not in the list: valves, sirens, and scripts, which can do anything (unlocking a door included). HA's Assist API has no alarm arm or disarm intent, so the alarm isn't controllable, and the agent says so.
- **Ordinary control runs without asking** (`default_tier="read"`), as the ticket asks and as ADR 0014 does for media. Dan can tighten any tool with `PUT /settings/permissions/home_assistant/<tool>`. Locks and garage doors are `irreversible` with no default tier, so only a signed approval from the paired app runs them (ADR 0016).
- **A fresh MCP session per call.** Each tool call runs initialize and `tools/call` over the `mcp` SDK's streamable HTTP client. On a connection failure it retries once after about 1 s, then answers "Home Assistant isn't reachable right now." With no long-lived session there's nothing to go stale when HA restarts. The next call works once HA is back. The cost is one extra round trip on the LAN per call. A 401 or 403 becomes "refused Alfred's access token", noted from the HTTP response because the MCP client hides the status.
- **Results.** HA's intent response is reduced to a short sentence ("Done: Lounge Lights.", "Failed: …", or HA's error speech). The live context is passed through, truncated to 6000 characters. Tools never raise.
- **Configuration.** `ALFRED_HOME_ASSISTANT_URL` (base URL) and `ALFRED_HOME_ASSISTANT_TIMEOUT_S`. The token is the connector's `api_key`, kept in the vault.

**Home specialist** (`agents/home.py`, `local-main`):
- rooms are areas; "downstairs" is a floor
- "the lights" with no room uses `RequestContext.active_room`, otherwise it asks which room
- heating is one thermostat and never prompts a room question
- state questions use `ha_get_state`
- locks and garage doors always go to their own tools, which wait for the app
- replies are one spoken sentence

Its classifier description is deliberately short: "lights, heating, blinds, door locks, garage door (Home Assistant)". With the 4B `local-router`, longer wording (scenes, states, "the home") pulled "plan a walking trip" and "something relaxing for dinner" to `home`, and even a comma changed which refusal requests it rated `complex`. Scenes still route here ("activate movie time"). Run the full eval set after changing it.

**Evals.** `cases/home.yaml` holds 13 cases, 5 of them in the smoke set. A new case field, `expect_confirmation: write|irreversible`, scores that a run paused with that tier (the `confirm` column), so "lock the front door" is checked to pause for an `irreversible` approval.

## Consequences

- Adding an HA capability (media players, vacuum, timers) means a new `ha_*` tool with its own risk, not a wider `ha_turn_on`.
- HA's argument names come from its Assist API (`name`, `area`, `floor`, `domain` and `device_class` as lists). If HA changes them, the tools need updating. The unit tests use a fake MCP server, so this needs Dan's check against the real HA.
- `ha_get_state` returns the whole live context. In a big house that's a long tool result for the local model. Truncation keeps it bounded, but filtering by area may be needed later.
- Locks and garage openers are often wired as relays (`switch.garage_door`). A switch call would run one without approval, and a model whose `ha_open_door` found no garage cover tends to retry with the switch. So:
  - `ha_turn_on`/`off` refuse `switch` and `input_boolean` calls whose name or area contains lock, door, gate, garage or alarm. Matching is by substring, so `clock` is refused too.
  - The agent is told never to fall back to them when a lock or door tool fails.
  - The setup doc says to expose only the proper `lock`/`cover` entity, never the relay.
  The name check is only a backstop: a relay called "opener" in the hall would still pass it.
- Scenes and switches run without asking, and HA runs whatever they do. A scene that sets a lock state, or a switch an automation watches, would bypass the lock tools' approval. `docs/connectors/home-assistant.md` says not to expose such scenes, and Dan can tighten `ha_turn_on` if he wants scenes to ask.
- Named rooms (STU-78) and Samsung TVs through HA (STU-77) build on this specialist.
