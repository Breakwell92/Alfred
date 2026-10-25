"""A fake Spotify Web API for connector and media-agent tests (`httpx.MockTransport`)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from alfred_server.connectors.builtin.spotify import SpotifyClient, SpotifyTools

KITCHEN = {"id": "dev-kitchen", "name": "Kitchen", "type": "Speaker", "is_active": False}
PHONE = {"id": "dev-phone", "name": "Dan's Pixel", "type": "Smartphone", "is_active": True}

DREAMS = {
    "type": "track",
    "id": "t-dreams",
    "uri": "spotify:track:t-dreams",
    "name": "Dreams",
    "artists": [{"name": "Fleetwood Mac"}],
    "album": {"name": "Rumours"},
}
FLEETWOOD = {"type": "artist", "uri": "spotify:artist:a-fm", "name": "Fleetwood Mac"}
DISCOVER = {"type": "playlist", "uri": "spotify:playlist:p-dw", "name": "Discover Weekly"}


def spotify_error(status: int, reason: str = "", message: str = "") -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"status": status, "message": message, "reason": reason}}
    )


class FakeSpotify:
    """Answers the Web API calls the toolkit makes; records each one.

    `routes` maps "METHOD /path" to a JSON body or an `httpx.Response`; anything unlisted
    answers 204 (Spotify's reply to a player command).
    """

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes: dict[str, Any] = {
            "GET /v1/me/player/devices": {"devices": [KITCHEN, PHONE]},
            "GET /v1/me/playlists": {"items": [DISCOVER, None]},
            "GET /v1/search": {
                "artists": {"items": [FLEETWOOD]},
                "tracks": {"items": [DREAMS]},
                "playlists": {"items": [None, DISCOVER]},
            },
            "GET /v1/me/player": {
                "is_playing": True,
                "device": PHONE,
                "item": DREAMS,
            },
            "GET /v1/me/player/currently-playing": {"is_playing": True, "item": DREAMS},
            **(routes or {}),
        }
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get(f"{request.method} {request.url.path}")
        if answer is None:
            return httpx.Response(204)
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)

    def tools(self) -> SpotifyTools:
        return SpotifyTools(SpotifyClient("test-access-token", httpx.MockTransport(self)))

    def calls(self, method: str | None = None) -> list[str]:
        """ "METHOD /path" of each request (only `method` ones if given)."""
        return [
            f"{r.method} {r.url.path.removeprefix('/v1')}"
            for r in self.requests
            if method is None or r.method == method
        ]

    def last(self, method: str, path: str) -> httpx.Request:
        return [r for r in self.requests if f"{r.method} {r.url.path}" == f"{method} /v1{path}"][-1]

    @staticmethod
    def json(request: httpx.Request) -> Any:
        return json.loads(request.content) if request.content else None
