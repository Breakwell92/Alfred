"""The Routines specialist: creates, lists and deletes routines ("when I say X, do Y").

Its tools are the `routines` connector's plus the read-only tools of the home and media
connectors, to look up real device, room and playlist names. It never gets the action tools:
creating "movie time" must not dim the lights. Running a routine needs no agent at all
(`alfred_server.routines`). Design: docs/decisions/0021-routines.md.
"""

from agno.agent import Agent
from agno.db.base import AsyncBaseDb, BaseDb
from agno.models.base import Model

from alfred_server.agents.base import build_agent
from alfred_server.agents.context import RequestContext

ROUTINES_AGENT_NAME = "routines"

# Specialists whose connector tools the routines agent loads, read-only (besides its own).
ROUTINE_SOURCES: tuple[str, ...] = ("home", "media", "general")

ROUTINES_INSTRUCTIONS = [
    "You set up Dan's routines: a phrase that runs a fixed list of actions instantly, without "
    "you. 'When I say movie time, dim the lounge lights and turn on the TV' creates one.",
    "To create one: call list_step_tools to see the tools a step can use and their arguments. "
    "Look up real names first when you need them (ha_get_state for rooms and devices, "
    "spotify_my_playlists or spotify_search for music). Then call save_routine once with "
    "concrete steps: each step's connector, tool and args exactly as list_step_tools gives "
    "them, with real values and no placeholders. 'Dim' with no number means brightness 30.",
    "You can't do the actions yourself, and you mustn't try: you only save them.",
    "The phrase is usually the routine's name. Keep the order Dan gave. If you can't find a "
    "device Dan named, use his words for it (Home Assistant matches names when the routine "
    "runs) rather than leaving the step out.",
    "If a tool Dan wants isn't in list_step_tools, say that service isn't connected or that "
    "it can't be in a routine (locks and garage doors always need approval in the app), and "
    "save the rest only if Dan asked for it anyway.",
    "Never ask Dan whether to save it, and never describe the routine instead of saving it: "
    "call save_routine in the same turn. It reads every step back to Dan and waits for his "
    "yes itself. After it runs, say in one sentence what was saved and the phrase to say. If "
    "it says something is wrong, explain briefly.",
    "To list routines call list_routines; to delete one call delete_routine with its name.",
]


def build_routines_agent(ctx: RequestContext, *, db: BaseDb | AsyncBaseDb, model: Model) -> Agent:
    return build_agent(
        ROUTINES_AGENT_NAME,
        ctx=ctx,
        alias="local-main",
        db=db,
        instructions=ROUTINES_INSTRUCTIONS,
        model=model,
    )
