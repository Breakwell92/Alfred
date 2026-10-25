"""The Media specialist: music on Spotify (STU-74); Plex and TV playback join it later.

Its tools come from the connector registry (`specialist="media"` connectors), which the router
loads per request and appends before gating. With Spotify not connected it has none, and says so.
"""

from agno.agent import Agent
from agno.db.base import AsyncBaseDb, BaseDb
from agno.models.base import Model

from alfred_server.agents.base import build_agent
from alfred_server.agents.context import RequestContext

MEDIA_AGENT_NAME = "media"

MEDIA_INSTRUCTIONS = [
    "You control Dan's music. Use a tool for every request; never claim something played, "
    "paused or changed without calling one.",
    "To play something call spotify_play with what Dan asked for as the query, e.g. "
    "'Fleetwood Mac' or 'Discover Weekly'. For a mood or occasion ('something relaxing for "
    "dinner') use a short query like 'relaxing dinner' with kind 'playlist'. When Dan says "
    "it's his ('my Discover Weekly', 'my running playlist'), set mine to true.",
    "When Dan names a speaker or device ('on the kitchen speaker'), pass it as device, as he "
    "said it. Otherwise leave device out.",
    "'Pause', 'stop the music' = spotify_pause. 'Carry on', 'resume' = spotify_play with no "
    "query. 'Skip', 'next song' = spotify_next. 'What's this song?' = spotify_now_playing.",
    "Volume: 'turn it up/down' means about 15 points from the current volume if you know it, "
    "otherwise set 60 for up and 30 for down.",
    "Reply with one short spoken sentence saying what happened, e.g. 'Playing Radiohead in "
    "the kitchen.' If a tool says something went wrong (no active device, Premium needed), "
    "say that plainly.",
    "If you have no music tools, say Spotify isn't connected yet and Dan can connect it in "
    "Alfred's connector settings.",
]


def build_media_agent(ctx: RequestContext, *, db: BaseDb | AsyncBaseDb, model: Model) -> Agent:
    return build_agent(
        MEDIA_AGENT_NAME,
        ctx=ctx,
        alias="local-main",
        db=db,
        instructions=MEDIA_INSTRUCTIONS,
        model=model,
    )
