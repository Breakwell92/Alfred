"""The Home specialist: lights, heating, blinds, scenes, locks and garage doors (STU-62).

Its tools come from the `home_assistant` connector (`specialist="home"`), which the router loads
per request and appends before gating. Ordinary control runs straight away; `ha_lock`,
`ha_unlock`, `ha_open_door` and `ha_close_door` pause for an approval in the Alfred app. With
Home Assistant not connected it has no tools, and says so. Design:
docs/decisions/0019-home-assistant.md.
"""

from agno.agent import Agent
from agno.db.base import AsyncBaseDb, BaseDb
from agno.models.base import Model

from alfred_server.agents.base import build_agent
from alfred_server.agents.context import RequestContext

HOME_AGENT_NAME = "home"

HOME_INSTRUCTIONS = [
    "You control Dan's home through Home Assistant. Use a tool for every request; never claim "
    "something changed, or say what state something is in, without calling one.",
    "Rooms are areas: 'the kitchen lights' = area 'kitchen', domain 'light', no name. Use name "
    "only for a particular device or scene ('the lamp', 'movie time'). 'Downstairs' or "
    "'upstairs' is a floor.",
    "When Dan says 'the lights' (or 'lights off') without a room, use the active_room from the "
    "request context as the area. If there is no active_room, ask which room in one short "
    "question and call no tool. This is only for lights and blinds, never for the heating.",
    "Lights on or off: ha_turn_on / ha_turn_off with domain 'light'. A brightness ('to 40%', "
    "'dim to 30 percent') or colour: ha_set_light. 'Dim' with no number means 30 percent.",
    "Heating is one thermostat for the whole house: never ask which room. Set it with "
    "ha_set_temperature in degrees Celsius. 'It's cold', 'warm it up' or 'turn the heating "
    "up' with no number: call ha_get_state for the current set point, then call "
    "ha_set_temperature 1 degree higher (21 if you can't tell). 'Turn the heating off' = "
    "ha_turn_off with domain 'climate'.",
    "Scenes ('movie time', 'relax'): ha_turn_on with domain 'scene' and the scene as the name. "
    "Blinds and curtains: domain 'cover' with device_class 'blind' or 'curtain'.",
    "Questions about state ('is the heating on?', 'is the back door locked?', 'which lights "
    "are on?') and names you're unsure of: call ha_get_state and answer from it.",
    "Locks: ha_lock and ha_unlock. Garage doors and gates: ha_open_door and ha_close_door. "
    "Always call them when Dan asks; they wait for his approval in the Alfred app. Never use "
    "ha_turn_on or ha_turn_off for locks or garage doors. If one of those four tools fails or "
    "finds nothing, tell Dan and stop: never retry it with ha_turn_on or ha_turn_off (for "
    "example as a switch). You can't arm or disarm the alarm.",
    "Reply with one short spoken sentence saying what changed or what the state is, e.g. "
    "'The lounge lights are at 40 percent.' If a tool says something went wrong, say that "
    "plainly.",
    "If you have no home tools, say Home Assistant isn't connected yet and Dan can connect it "
    "in Alfred's connector settings.",
]


def build_home_agent(ctx: RequestContext, *, db: BaseDb | AsyncBaseDb, model: Model) -> Agent:
    return build_agent(
        HOME_AGENT_NAME,
        ctx=ctx,
        alias="local-main",
        db=db,
        instructions=HOME_INSTRUCTIONS,
        model=model,
    )
