# Home Assistant

Connector id `home_assistant` (`server/alfred_server/connectors/builtin/home_assistant.py`), used by the `home` specialist. Design: [ADR 0017](../decisions/0017-home-assistant.md).

Alfred talks to Home Assistant's MCP server and only ever calls HA's intents through its own `ha_*` tools, each limited to fixed device domains. Lights, heating, blinds and scenes run straight away. Locks and garage doors wait for an approval in the paired Alfred app.

## 1. Set up Home Assistant

1. **Enable the MCP server.** In HA, go to **Settings → Devices & services → Add integration → Model Context Protocol Server**. Keep the default LLM API (**Assist**). Alfred uses the endpoint `http://<ha-host>:8123/api/mcp`.
2. **Expose the devices.** Alfred can only see and control entities exposed to Assist: **Settings → Voice assistants → Expose**. Expose the lights, thermostat, blinds, scenes, locks and garage door you want Alfred to use. Give each a clear name and alias, and assign it an area (room) and floor. Alfred controls rooms by area ("the kitchen lights") and floors by floor ("downstairs").
3. **Set cover device classes.** Blinds and curtains need a device class of `blind`, `curtain`, `shade`, `shutter`, `awning` or `window`. Garage doors and gates need `garage`, `gate` or `door`. A cover with no device class can't be moved by Alfred at all, which is deliberate: see the ADR.
4. **Create a long-lived access token.** Open your **Profile → Security → Long-lived access tokens → Create token**. Name it "Alfred" and copy it. It is shown once.

## 2. Configure Alfred

In `server/.env` (and `infra/.env` when the server runs in compose):

```bash
ALFRED_HOME_ASSISTANT_URL=http://homeassistant.local:8123   # base URL, no /api/mcp
# ALFRED_HOME_ASSISTANT_TIMEOUT_S=10
```

Restart the server, then store the token (it goes into the encrypted vault) and enable the connector:

```bash
TOKEN=...   # ALFRED_API_TOKEN
curl -X PUT -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"enabled": true, "api_key": "<HA long-lived token>"}' \
  http://glados:8000/connectors/home_assistant
```

`GET /connectors` should now show `home_assistant` as `connected`.

## 3. Try it

In `/ui/chat`:
- "Turn the lounge lights to 40%."
- "Is the back door locked?"
- "Turn the heating up to 21."
- "Lock the front door." The run pauses; approve it in the Alfred app (an `irreversible` tool, so only a signed approval from the paired app counts, ADR 0016).

If a tool says Home Assistant isn't reachable, check the URL from the server: `curl -H "Authorization: Bearer <HA token>" http://homeassistant.local:8123/api/` should answer `{"message": "API running."}`. "Refused Alfred's access token" means the token was revoked; create a new one and `PUT` it again.

## Tools

| Tool | What | Tier |
| --- | --- | --- |
| `ha_get_state` | Names, rooms and states of every exposed device (HA's live context) | read (kept local) |
| `ha_turn_on`, `ha_turn_off` | Lights, switches, fans, media players, scenes, input booleans, heating, blinds and curtains | runs without asking |
| `ha_set_light` | Brightness (0–100 %) and colour | runs without asking |
| `ha_set_temperature` | Thermostat set point, 5–30 °C | runs without asking |
| `ha_lock`, `ha_unlock` | Door locks | approval in the app |
| `ha_open_door`, `ha_close_door` | Garage doors, gates, motorised doors | approval in the app |

Not available: arming or disarming the alarm (HA's Assist API has no intent for it), valves, scripts, and anything not exposed to Assist.

**Don't expose a scene (or a switch that triggers an automation) that locks, unlocks or opens anything.** Scenes run without asking, and HA does whatever the scene contains, so it would bypass the approval the lock and garage tools need. To make ordinary control ask first, tighten it: `PUT /settings/permissions/home_assistant/ha_turn_on {"risk": "write"}`.

## Recovery

Every tool call opens a new MCP session, so nothing needs reconnecting after HA restarts. While HA is down, tools answer "Home Assistant isn't reachable right now" (after one retry); the next request after it's back works.
