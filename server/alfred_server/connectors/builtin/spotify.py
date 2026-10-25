"""`spotify` connector: playback control and search over the Spotify Web API (STU-74).

A small async toolkit of our own rather than Agno's `SpotifyTools` (sync, and no pause, skip,
volume or device targeting). Playback control needs Spotify Premium and targets one of the
account's "devices" (phones, speakers, desktop apps) by name. Every tool answers with a short
sentence the media agent can say, including for errors (no active device, not Premium), and
never raises. Setup: docs/connectors/spotify.md. Why: docs/decisions/0014-spotify-media.md.
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Literal

import httpx
from agno.tools import Toolkit

from alfred_server.connectors.models import (
    AgnoToolkitSource,
    Connector,
    Credentials,
    OAuth2Config,
    ToolError,
    ToolMeta,
)

logger = logging.getLogger(__name__)

API_URL = "https://api.spotify.com/v1"
TIMEOUT_S = 10.0

SearchKind = Literal["track", "artist", "album", "playlist"]
RepeatMode = Literal["track", "context", "off"]

NO_DEVICE = "No Spotify device is active. Open Spotify on your phone or a speaker and try again."
NOT_PREMIUM = "Spotify only allows playback control with a Premium account."
SIGNED_OUT = "Spotify needs signing in again. Reconnect it in Alfred's connector settings."
BUSY = "Spotify is busy right now. Try again in a moment."

# Words people add to a device name that Spotify's name won't have ("the kitchen speaker").
_FILLER = {"the", "my", "a", "in", "on", "speaker", "speakers", "device", "spotify"}
_MATCH_CUTOFF = 0.6
# How many of Dan's playlists to look through (Spotify pages them 50 at a time).
PLAYLIST_PAGE = 50
MAX_PLAYLISTS = 300


class SpotifyError(Exception):
    """A Spotify API failure, with the sentence to say about it."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _normalise(name: str) -> str:
    words = re.sub(r"[^\w\s]", " ", name.casefold()).split()
    kept = [w for w in words if w not in _FILLER]
    return " ".join(kept or words)


def match_devices(name: str, devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The devices a spoken `name` could mean: one, several (ambiguous) or none.

    Tries an exact match (ignoring case, punctuation and words like "the" and "speaker"),
    then one name containing the other, then shared words, then close spelling, and stops at
    the first of those that matches anything.
    """
    wanted = _normalise(name)
    named = [(d, _normalise(str(d.get("name", "")))) for d in devices]
    for checks in (
        lambda n: n == wanted,
        lambda n: bool(n) and (wanted in n or n in wanted),
        lambda n: bool(set(n.split()) & set(wanted.split())),
    ):
        hits = [d for d, n in named if checks(n)]
        if hits:
            return hits
    scored = [(difflib.SequenceMatcher(None, wanted, n).ratio(), d) for d, n in named]
    best = max(scored, key=lambda s: s[0], default=None)
    if best is not None and best[0] >= _MATCH_CUTOFF:
        return [best[1]]
    return []


def _or_list(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def _artists(item: dict[str, Any]) -> str:
    return ", ".join(a.get("name", "") for a in item.get("artists") or [] if a)


def _describe(item: dict[str, Any], *, owner: bool = False) -> str:
    """ "Dreams by Fleetwood Mac" for a track or album, the name for an artist or playlist.

    `owner` adds whose playlist it is, for one found by searching rather than in Dan's library.
    """
    name = str(item.get("name", "something"))
    by = _artists(item)
    if item.get("type") == "playlist":
        who = (item.get("owner") or {}).get("display_name") if owner else None
        return f"the playlist {name} by {who}" if who else f"the playlist {name}"
    return f"{name} by {by}" if by else name


class SpotifyClient:
    """Async calls to the Web API with one access token. `transport` is for tests."""

    def __init__(self, access_token: str, transport: httpx.AsyncBaseTransport | None = None):
        self._token = access_token
        self._transport = transport

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """The response's JSON, None for an empty one. Raises `SpotifyError`."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            async with httpx.AsyncClient(
                base_url=API_URL,
                timeout=TIMEOUT_S,
                transport=self._transport,
                headers={"Authorization": f"Bearer {self._token}"},
            ) as client:
                response = await client.request(method, path, params=params, json=json)
        except httpx.HTTPError as exc:
            logger.warning("spotify %s %s failed: %s", method, path, exc)
            raise SpotifyError("I couldn't reach Spotify.") from exc
        if response.status_code >= 400:
            raise _error(response)
        if response.status_code == 204 or not response.content:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None


def _error(response: httpx.Response) -> SpotifyError:
    status = response.status_code
    reason = message = ""
    try:
        error = response.json().get("error") or {}
        if isinstance(error, dict):
            reason = str(error.get("reason") or "")
            message = str(error.get("message") or "")
    except (ValueError, AttributeError):
        pass
    logger.info("spotify error %s %s: %s", status, reason, message)
    if reason == "NO_ACTIVE_DEVICE" or (status == 404 and "device" in message.casefold()):
        return SpotifyError(NO_DEVICE, status)
    if reason == "PREMIUM_REQUIRED" or (status == 403 and "premium" in message.casefold()):
        return SpotifyError(NOT_PREMIUM, status)
    if status == 401:
        return SpotifyError(SIGNED_OUT, status)
    if status == 429:
        return SpotifyError(BUSY, status)
    if status == 404:
        return SpotifyError("Spotify couldn't find that.", status)
    if status == 403:
        return SpotifyError("Spotify won't allow that right now.", status)
    return SpotifyError("Spotify had a problem with that request.", status)


class SpotifyTools(Toolkit):
    """The `spotify_*` tools. Each returns a sentence to tell Dan, errors included."""

    def __init__(self, client: SpotifyClient) -> None:
        self.client = client
        super().__init__(
            name="spotify",
            tools=[
                self.spotify_now_playing,
                self.spotify_devices,
                self.spotify_search,
                self.spotify_my_playlists,
                self.spotify_play,
                self.spotify_pause,
                self.spotify_next,
                self.spotify_previous,
                self.spotify_volume,
                self.spotify_shuffle,
                self.spotify_repeat,
                self.spotify_transfer,
                self.spotify_queue,
                self.spotify_like_current,
            ],
        )

    # --- Helpers ------------------------------------------------------------------------------

    async def _devices(self) -> list[dict[str, Any]]:
        body = await self.client.request("GET", "/me/player/devices") or {}
        return [d for d in body.get("devices") or [] if isinstance(d, dict)]

    async def _device(self, device: str | None) -> dict[str, Any] | None:
        """The device called `device`; None (the active one) if no name is given.

        Raises `SpotifyError` saying which devices there are if none matches, or asking which
        one if several do.
        """
        if not device:
            return None
        devices = await self._devices()
        if not devices:
            raise SpotifyError(NO_DEVICE)
        found = [d for d in match_devices(device, devices) if d.get("id")]
        if not found:
            names = ", ".join(str(d.get("name")) for d in devices)
            raise SpotifyError(f"I can't see a Spotify device called {device}. There's {names}.")
        if len(found) > 1:
            names = _or_list([str(d.get("name")) for d in found])
            raise SpotifyError(f"Did you mean {names}?")
        if found[0].get("is_restricted"):
            raise SpotifyError(f"Spotify can't be controlled on {found[0].get('name')}.")
        return found[0]

    @staticmethod
    def _id(device: dict[str, Any] | None) -> str | None:
        return str(device["id"]) if device else None

    @staticmethod
    def _on(device: dict[str, Any] | None) -> str:
        return f" on {device.get('name')}" if device else ""

    async def _search(self, query: str, kinds: str, limit: int = 5) -> dict[str, list[Any]]:
        body = await self.client.request(
            "GET", "/search", params={"q": query, "type": kinds, "limit": limit}
        )
        found: dict[str, list[Any]] = {}
        for kind in kinds.split(","):
            items = ((body or {}).get(f"{kind}s") or {}).get("items") or []
            # Spotify returns null entries for items it hides (e.g. some playlists).
            found[kind] = [i for i in items if isinstance(i, dict)]
        return found

    async def _playlists(self) -> list[dict[str, Any]]:
        """Dan's playlists (owned and followed), up to `MAX_PLAYLISTS`."""
        playlists: list[dict[str, Any]] = []
        for offset in range(0, MAX_PLAYLISTS, PLAYLIST_PAGE):
            body = await self.client.request(
                "GET", "/me/playlists", params={"limit": PLAYLIST_PAGE, "offset": offset}
            )
            page = (body or {}).get("items") or []
            playlists += [p for p in page if isinstance(p, dict)]
            if not page or not (body or {}).get("next"):
                break
        return playlists

    async def _my_playlist(self, name: str) -> dict[str, Any] | None:
        playlists = await self._playlists()
        wanted = _normalise(name).removesuffix(" playlist")
        by_name = {_normalise(str(p.get("name", ""))): p for p in playlists}
        if wanted in by_name:
            return by_name[wanted]
        close = difflib.get_close_matches(wanted, list(by_name), n=1, cutoff=0.8)
        return by_name[close[0]] if close else None

    async def _find(
        self, query: str, kind: SearchKind | None, mine: bool
    ) -> tuple[dict[str, Any] | None, bool]:
        """What "play <query>" most likely means: Dan's own playlist, an artist or a track.

        Returns the item and whether it's from Dan's library. With `mine` only his playlists
        are looked at, so a stranger's playlist of the same name is never played.
        """
        if mine or kind in (None, "playlist"):
            own = await self._my_playlist(query)
            if own is not None or mine:
                return own, own is not None
        if kind is not None:
            # Not limit=1: Spotify counts the null entries it hides towards the limit.
            items = (await self._search(query, kind)).get(kind) or []
            return (items[0] if items else None), False
        found = await self._search(query, "artist,track", limit=1)
        artist = next(iter(found.get("artist") or []), None)
        if artist is not None:
            ratio = difflib.SequenceMatcher(
                None, _normalise(query), _normalise(str(artist.get("name", "")))
            ).ratio()
            if ratio >= 0.8:
                return artist, False
        track = next(iter(found.get("track") or []), None)
        return track or artist, False

    # --- Read ---------------------------------------------------------------------------------

    async def spotify_now_playing(self) -> str:
        """Say what's playing on Spotify right now ("what's this song?").

        Returns:
            The track, artist and album, and the device it's playing on, or that nothing is.
        """
        try:
            body = await self.client.request("GET", "/me/player")
        except SpotifyError as e:
            return ToolError(e.message)
        item = (body or {}).get("item")
        if not body or not isinstance(item, dict):
            return "Nothing is playing on Spotify."
        album = (item.get("album") or {}).get("name")
        device = (body.get("device") or {}).get("name")
        state = "Playing" if body.get("is_playing") else "Paused"
        text = f"{state}: {_describe(item)}"
        if album and item.get("type") == "track":
            text += f", from {album}"
        return text + (f", on {device}." if device else ".")

    async def spotify_devices(self) -> str:
        """List the Spotify devices (phones, speakers, computers) that can play right now.

        Returns:
            Each device's name and type, marking the active one.
        """
        try:
            devices = await self._devices()
        except SpotifyError as e:
            return ToolError(e.message)
        if not devices:
            return NO_DEVICE
        parts = [
            f"{d.get('name')} ({str(d.get('type', 'device')).lower()}"
            + (", active)" if d.get("is_active") else ")")
            for d in devices
        ]
        return "Spotify devices: " + "; ".join(parts) + "."

    async def spotify_search(self, query: str, kind: SearchKind = "track") -> str:
        """Search Spotify without playing anything.

        Args:
            query: What to look for, e.g. "Fleetwood Mac" or "Dreams".
            kind: "track", "artist", "album" or "playlist".

        Returns:
            Up to five matches.
        """
        try:
            items = (await self._search(query, kind)).get(kind) or []
        except SpotifyError as e:
            return ToolError(e.message)
        if not items:
            return f"Spotify found no {kind}s for {query}."
        return f"Spotify {kind}s for {query}: " + "; ".join(_describe(i) for i in items) + "."

    async def spotify_my_playlists(self) -> str:
        """List Dan's own Spotify playlists (the ones in his library).

        Returns:
            The playlists' names.
        """
        try:
            names = [str(p.get("name")) for p in await self._playlists()]
        except SpotifyError as e:
            return ToolError(e.message)
        if not names:
            return "You don't have any Spotify playlists."
        return "Your playlists: " + "; ".join(names) + "."

    # --- Playback -------------------------------------------------------------------------------

    async def spotify_play(
        self,
        query: str | None = None,
        kind: SearchKind | None = None,
        device: str | None = None,
        mine: bool = False,
    ) -> str:
        """Play music on Spotify, or resume what was playing if no query is given.

        Args:
            query: What to play: an artist, song, album, playlist (including Dan's own, e.g.
                "Discover Weekly") or a mood ("relaxing dinner music"). Leave out to resume.
            kind: "track", "artist", "album" or "playlist" if Dan said which; leave out
                otherwise. Use "playlist" for moods.
            device: The speaker or device to play on, as Dan said it (e.g. "kitchen");
                leave out for the one already playing.
            mine: True when Dan says it's his ("my Discover Weekly", "my running playlist"):
                only his own playlists are played, never someone else's of the same name.

        Returns:
            What started playing and where.
        """
        try:
            target = await self._device(device)
            body: dict[str, Any] | None = None
            what = "Resumed Spotify"
            if query:
                item, own = await self._find(query, kind, mine)
                if item is None and mine:
                    return ToolError(
                        f"I can't find {query} in your Spotify playlists. Spotify hides some of "
                        "its own playlists from apps; follow or copy it to make it findable."
                    )
                if item is None or not item.get("uri"):
                    return ToolError(f"I couldn't find {query} on Spotify.")
                uri = str(item["uri"])
                body = {"uris": [uri]} if item.get("type") == "track" else {"context_uri": uri}
                what = f"Playing {_describe(item, owner=not own)}"
            await self.client.request(
                "PUT", "/me/player/play", params={"device_id": self._id(target)}, json=body
            )
            return what + self._on(target) + "."
        except SpotifyError as e:
            return ToolError(e.message)

    async def spotify_pause(self) -> str:
        """Pause Spotify.

        Returns:
            Whether it paused.
        """
        try:
            await self.client.request("PUT", "/me/player/pause")
        except SpotifyError as e:
            return ToolError(e.message)
        return "Paused."

    async def spotify_next(self) -> str:
        """Skip to the next track on Spotify.

        Returns:
            Whether it skipped.
        """
        try:
            await self.client.request("POST", "/me/player/next")
        except SpotifyError as e:
            return ToolError(e.message)
        return "Skipped to the next track."

    async def spotify_previous(self) -> str:
        """Go back to the previous track on Spotify.

        Returns:
            Whether it went back.
        """
        try:
            await self.client.request("POST", "/me/player/previous")
        except SpotifyError as e:
            return ToolError(e.message)
        return "Back to the previous track."

    async def spotify_volume(self, percent: int, device: str | None = None) -> str:
        """Set Spotify's volume.

        Args:
            percent: Volume from 0 to 100.
            device: The device, as Dan said it; leave out for the one playing.

        Returns:
            The new volume.
        """
        percent = max(0, min(100, int(percent)))
        try:
            target = await self._device(device)
            await self.client.request(
                "PUT",
                "/me/player/volume",
                params={"volume_percent": percent, "device_id": self._id(target)},
            )
            return f"Volume set to {percent}%{self._on(target)}."
        except SpotifyError as e:
            return ToolError(e.message)

    async def spotify_shuffle(self, on: bool) -> str:
        """Turn Spotify's shuffle on or off.

        Args:
            on: True to shuffle, False to play in order.

        Returns:
            The new shuffle setting.
        """
        try:
            await self.client.request(
                "PUT", "/me/player/shuffle", params={"state": "true" if on else "false"}
            )
        except SpotifyError as e:
            return ToolError(e.message)
        return "Shuffle on." if on else "Shuffle off."

    async def spotify_repeat(self, mode: RepeatMode) -> str:
        """Set Spotify's repeat mode.

        Args:
            mode: "track" (repeat this song), "context" (repeat the album or playlist) or "off".

        Returns:
            The new repeat setting.
        """
        try:
            await self.client.request("PUT", "/me/player/repeat", params={"state": mode})
        except SpotifyError as e:
            return ToolError(e.message)
        return {
            "track": "Repeating this track.",
            "context": "Repeating.",
            "off": "Repeat off.",
        }.get(mode, "Repeat set.")

    async def spotify_transfer(self, device: str) -> str:
        """Move what's playing on Spotify to another device and keep playing.

        Args:
            device: The device to move to, as Dan said it (e.g. "living room").

        Returns:
            Where it's playing now.
        """
        try:
            target = await self._device(device)
            if target is None:
                return ToolError("Which device should I move it to?")
            await self.client.request(
                "PUT", "/me/player", json={"device_ids": [self._id(target)], "play": True}
            )
            return f"Moved to {target.get('name') or device}."
        except SpotifyError as e:
            return ToolError(e.message)

    async def spotify_queue(self, query: str) -> str:
        """Add a song to the Spotify queue, to play after the current one.

        Args:
            query: The song, e.g. "Dreams by Fleetwood Mac".

        Returns:
            What was queued.
        """
        try:
            tracks = (await self._search(query, "track", limit=1)).get("track") or []
            if not tracks or not tracks[0].get("uri"):
                return ToolError(f"I couldn't find {query} on Spotify.")
            await self.client.request(
                "POST", "/me/player/queue", params={"uri": str(tracks[0]["uri"])}
            )
            return f"Queued {_describe(tracks[0])}."
        except SpotifyError as e:
            return ToolError(e.message)

    async def spotify_like_current(self) -> str:
        """Save the song that's playing to Dan's Liked Songs.

        Returns:
            Which song was liked.
        """
        try:
            body = await self.client.request("GET", "/me/player/currently-playing")
            item = (body or {}).get("item")
            if not isinstance(item, dict) or item.get("type") != "track" or not item.get("id"):
                return "Nothing is playing to like."
            await self.client.request("PUT", "/me/tracks", json={"ids": [item["id"]]})
            return f"Added {_describe(item)} to your Liked Songs."
        except SpotifyError as e:
            return ToolError(e.message)


def build_spotify_toolkit(creds: Credentials) -> Toolkit:
    return SpotifyTools(SpotifyClient(creds.access_token or ""))


def _control(name: str, description: str, summary: str) -> ToolMeta:
    # Ordinary media control: it changes playback (so `write`) but runs without asking,
    # as Dan asked in STU-74. He can tighten any of these in /settings/permissions.
    return ToolMeta(
        name=name,
        description=description,
        risk="write",
        default_tier="read",
        confirm_summary=summary,
    )


SPOTIFY_CONNECTOR = Connector(
    id="spotify",
    name="Spotify",
    description="Play, pause and search music on Spotify (playback control needs Premium).",
    auth_type="oauth2",
    scopes=(
        "user-read-playback-state",
        "user-modify-playback-state",
        "user-read-currently-playing",
        "playlist-read-private",
        "user-library-read",
        "user-library-modify",
    ),
    oauth=OAuth2Config(
        authorize_url="https://accounts.spotify.com/authorize",
        token_url="https://accounts.spotify.com/api/token",
    ),
    tool_source=AgnoToolkitSource(build_spotify_toolkit),
    tools=(
        ToolMeta(name="spotify_now_playing", description="What's playing now.", risk="read"),
        ToolMeta(name="spotify_devices", description="Devices that can play.", risk="read"),
        ToolMeta(name="spotify_search", description="Search Spotify.", risk="read"),
        ToolMeta(name="spotify_my_playlists", description="Dan's playlists.", risk="read"),
        _control("spotify_play", "Play music or resume.", "Play {query} on Spotify"),
        _control("spotify_pause", "Pause playback.", "Pause Spotify"),
        _control("spotify_next", "Skip to the next track.", "Skip to the next track"),
        _control("spotify_previous", "Previous track.", "Go back to the previous track"),
        _control("spotify_volume", "Set the volume.", "Set Spotify's volume to {percent}%"),
        _control("spotify_shuffle", "Shuffle on or off.", "Set Spotify's shuffle to {on}"),
        _control("spotify_repeat", "Set repeat mode.", "Set Spotify's repeat to {mode}"),
        _control("spotify_transfer", "Move playback.", "Move Spotify to {device}"),
        _control("spotify_queue", "Queue a track.", "Queue {query} on Spotify"),
        _control("spotify_like_current", "Like the current track.", "Like the current song"),
    ),
    specialist="media",
)
