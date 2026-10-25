import asyncio
from typing import Any

import httpx
import pytest

from alfred_server.confirmations.policy import ToolPolicy
from alfred_server.connectors.builtin.spotify import (
    BUSY,
    NO_DEVICE,
    NOT_PREMIUM,
    SIGNED_OUT,
    SPOTIFY_CONNECTOR,
    build_spotify_toolkit,
    match_devices,
)
from alfred_server.connectors.models import AgnoToolkitSource, Connector, Credentials, ToolMeta
from alfred_server.connectors.registry import ConnectorRegistry, default_registry
from tests.spotify_fakes import DISCOVER, KITCHEN, PHONE, STRANGERS, FakeSpotify, spotify_error


def run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def spotify() -> FakeSpotify:
    return FakeSpotify()


# --- Connector definition -----------------------------------------------------------------------


def test_spotify_is_a_builtin_oauth_connector_for_media() -> None:
    connector = default_registry().get("spotify")

    assert connector is SPOTIFY_CONNECTOR
    assert connector.auth_type == "oauth2"
    assert connector.specialist == "media"
    assert "user-modify-playback-state" in connector.scopes
    assert connector.oauth is not None
    assert connector.oauth.token_url == "https://accounts.spotify.com/api/token"


def test_toolkit_exposes_exactly_the_declared_tools() -> None:
    toolkit = build_spotify_toolkit(Credentials(access_token="x"))

    assert sorted(toolkit.async_functions) == sorted(SPOTIFY_CONNECTOR.tool_names)
    assert toolkit.functions == {}


def test_every_tool_is_honestly_declared() -> None:
    risks = {t.name: (t.risk, t.tier) for t in SPOTIFY_CONNECTOR.tools}

    assert risks["spotify_now_playing"] == ("read", "read")
    assert risks["spotify_search"] == ("read", "read")
    # Playback control changes things (write) but runs without asking by default.
    assert risks["spotify_play"] == ("write", "read")
    assert risks["spotify_like_current"] == ("write", "read")
    for meta in SPOTIFY_CONNECTOR.tools:
        if meta.risk != "read":
            assert meta.confirm_summary, meta.name


# --- Default tiers ------------------------------------------------------------------------------


def test_default_tier_applies_without_an_override() -> None:
    policy = ToolPolicy(default_registry())

    assert policy.tier("spotify_pause") == "read"
    assert not policy.gated("spotify_pause")
    assert policy.gated("send_test_message")  # `write` without a default tier still asks


def test_an_override_beats_the_default_tier() -> None:
    policy = ToolPolicy(default_registry(), {("spotify", "spotify_play"): "write"})

    assert policy.gated("spotify_play")
    assert not policy.gated("spotify_pause")


def test_default_tier_above_risk_is_rejected() -> None:
    connector = Connector(
        id="sneaky",
        name="Sneaky",
        description="",
        auth_type="none",
        tool_source=AgnoToolkitSource(lambda _c: build_spotify_toolkit(Credentials())),
        tools=(ToolMeta(name="peek", description="", risk="read", default_tier="write"),),
        specialist="general",
    )

    with pytest.raises(ValueError, match="default_tier"):
        ConnectorRegistry([connector])


# --- Device names -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("kitchen", "Kitchen"),
        ("the kitchen speaker", "Kitchen"),
        ("Kitchen.", "Kitchen"),
        ("my phone", None),  # no shared word, not close in spelling
        ("pixel", "Dan's Pixel"),
        ("kitchn", "Kitchen"),
        ("garage", None),
    ],
)
def test_match_device(spoken: str, expected: str | None) -> None:
    found = match_devices(spoken, [KITCHEN, PHONE])

    assert [d["name"] for d in found] == ([expected] if expected else [])


def test_match_device_with_no_devices() -> None:
    assert match_devices("kitchen", []) == []


# --- Playback -----------------------------------------------------------------------------------


def test_play_my_playlist_on_a_named_device(spotify: FakeSpotify) -> None:
    result = run(spotify.tools().spotify_play("Discover Weekly", device="the kitchen speaker"))

    assert result == "Playing the playlist Discover Weekly on Kitchen."
    play = spotify.last("PUT", "/me/player/play")
    assert play.url.params["device_id"] == "dev-kitchen"
    assert spotify.json(play) == {"context_uri": DISCOVER["uri"]}
    assert "GET /search" not in spotify.calls()  # found in Dan's own playlists first


def test_play_an_artist(spotify: FakeSpotify) -> None:
    result = run(spotify.tools().spotify_play("fleetwood mac"))

    assert result == "Playing Fleetwood Mac."
    play = spotify.last("PUT", "/me/player/play")
    assert spotify.json(play) == {"context_uri": "spotify:artist:a-fm"}
    assert "device_id" not in play.url.params


def test_play_a_song_by_name_plays_the_track(spotify: FakeSpotify) -> None:
    result = run(spotify.tools().spotify_play("dreams"))

    assert result == "Playing Dreams by Fleetwood Mac."
    assert spotify.json(spotify.last("PUT", "/me/player/play")) == {
        "uris": ["spotify:track:t-dreams"]
    }


def test_play_a_mood_searches_playlists_and_says_whose(spotify: FakeSpotify) -> None:
    result = run(spotify.tools().spotify_play("relaxing dinner", kind="playlist"))

    # Hidden (null) results are skipped; a playlist from search isn't Dan's, so say whose.
    assert result == "Playing the playlist Chill Dinner by jo_b."
    assert spotify.json(spotify.last("PUT", "/me/player/play")) == {"context_uri": STRANGERS["uri"]}
    assert spotify.last("GET", "/search").url.params["type"] == "playlist"


def test_play_without_a_query_resumes(spotify: FakeSpotify) -> None:
    result = run(spotify.tools().spotify_play())

    assert result == "Resumed Spotify."
    assert spotify.last("PUT", "/me/player/play").content == b""


def test_play_on_an_unknown_device_names_the_ones_there_are(spotify: FakeSpotify) -> None:
    result = run(spotify.tools().spotify_play("Dreams", device="garage"))

    assert result == "I can't see a Spotify device called garage. There's Kitchen, Dan's Pixel."
    assert spotify.calls("PUT") == []


def test_play_on_a_device_when_none_are_listed() -> None:
    spotify = FakeSpotify({"GET /v1/me/player/devices": {"devices": []}})

    assert run(spotify.tools().spotify_play("Dreams", device="kitchen")) == NO_DEVICE


def test_nothing_found(spotify: FakeSpotify) -> None:
    spotify.routes["GET /v1/search"] = {"tracks": {"items": []}, "artists": {"items": []}}
    spotify.routes["GET /v1/me/playlists"] = {"items": []}

    assert run(spotify.tools().spotify_play("zzzz")) == "I couldn't find zzzz on Spotify."


@pytest.mark.parametrize(
    ("method", "path", "call", "said"),
    [
        ("PUT", "/me/player/pause", lambda t: t.spotify_pause(), "Paused."),
        ("POST", "/me/player/next", lambda t: t.spotify_next(), "Skipped to the next track."),
        (
            "POST",
            "/me/player/previous",
            lambda t: t.spotify_previous(),
            "Back to the previous track.",
        ),
        ("PUT", "/me/player/shuffle", lambda t: t.spotify_shuffle(True), "Shuffle on."),
        ("PUT", "/me/player/repeat", lambda t: t.spotify_repeat("off"), "Repeat off."),
    ],
)
def test_simple_controls(
    spotify: FakeSpotify, method: str, path: str, call: Any, said: str
) -> None:
    assert run(call(spotify.tools())) == said
    assert spotify.calls() == [f"{method} {path}"]


def test_volume_is_clamped_and_targets_a_device(spotify: FakeSpotify) -> None:
    result = run(spotify.tools().spotify_volume(150, device="kitchen"))

    assert result == "Volume set to 100% on Kitchen."
    params = spotify.last("PUT", "/me/player/volume").url.params
    assert params["volume_percent"] == "100"
    assert params["device_id"] == "dev-kitchen"


def test_transfer(spotify: FakeSpotify) -> None:
    result = run(spotify.tools().spotify_transfer("kitchen"))

    assert result == "Moved to Kitchen."
    assert spotify.json(spotify.last("PUT", "/me/player")) == {
        "device_ids": ["dev-kitchen"],
        "play": True,
    }


def test_queue(spotify: FakeSpotify) -> None:
    result = run(spotify.tools().spotify_queue("dreams"))

    assert result == "Queued Dreams by Fleetwood Mac."
    assert spotify.last("POST", "/me/player/queue").url.params["uri"] == "spotify:track:t-dreams"


def test_like_current(spotify: FakeSpotify) -> None:
    result = run(spotify.tools().spotify_like_current())

    assert result == "Added Dreams by Fleetwood Mac to your Liked Songs."
    assert spotify.json(spotify.last("PUT", "/me/tracks")) == {"ids": ["t-dreams"]}


# --- Reading ------------------------------------------------------------------------------------


def test_now_playing(spotify: FakeSpotify) -> None:
    result = run(spotify.tools().spotify_now_playing())

    assert result == "Playing: Dreams by Fleetwood Mac, from Rumours, on Dan's Pixel."


def test_now_playing_when_nothing_is() -> None:
    spotify = FakeSpotify({"GET /v1/me/player": httpx.Response(204)})

    assert run(spotify.tools().spotify_now_playing()) == "Nothing is playing on Spotify."


def test_devices(spotify: FakeSpotify) -> None:
    result = run(spotify.tools().spotify_devices())

    assert result == "Spotify devices: Kitchen (speaker); Dan's Pixel (smartphone, active)."


def test_search_and_playlists(spotify: FakeSpotify) -> None:
    tools = spotify.tools()

    assert (
        run(tools.spotify_search("dreams")) == "Spotify tracks for dreams: Dreams by Fleetwood Mac."
    )
    assert run(tools.spotify_my_playlists()) == "Your playlists: Discover Weekly."


def test_the_access_token_is_sent(spotify: FakeSpotify) -> None:
    run(spotify.tools().spotify_pause())

    assert spotify.requests[0].headers["Authorization"] == "Bearer test-access-token"


# --- Errors -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "said"),
    [
        (
            spotify_error(404, "NO_ACTIVE_DEVICE", "Player command failed: No active device"),
            NO_DEVICE,
        ),
        (
            spotify_error(403, "PREMIUM_REQUIRED", "Player command failed: Premium required"),
            NOT_PREMIUM,
        ),
        (spotify_error(401, message="The access token expired"), SIGNED_OUT),
        (spotify_error(429), BUSY),
        (httpx.Response(500, text="oops"), "Spotify had a problem with that request."),
    ],
)
def test_errors_are_said_not_raised(response: httpx.Response, said: str) -> None:
    spotify = FakeSpotify({"PUT /v1/me/player/pause": response})

    assert run(spotify.tools().spotify_pause()) == said


def test_network_failure() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    from alfred_server.connectors.builtin.spotify import SpotifyClient, SpotifyTools

    tools = SpotifyTools(SpotifyClient("t", httpx.MockTransport(fail)))

    assert run(tools.spotify_pause()) == "I couldn't reach Spotify."


# --- Review round 1 (PR #18) ----------------------------------------------------------------


def test_my_playlist_never_plays_a_strangers_one(spotify: FakeSpotify) -> None:
    spotify.routes["GET /v1/me/playlists"] = {"items": []}  # Spotify hid Discover Weekly

    result = run(spotify.tools().spotify_play("Discover Weekly", mine=True))

    assert result.startswith("I can't find Discover Weekly in your Spotify playlists.")
    assert "GET /search" not in spotify.calls()
    assert spotify.calls("PUT") == []


def test_my_playlist_still_plays_when_found(spotify: FakeSpotify) -> None:
    result = run(spotify.tools().spotify_play("Discover Weekly", mine=True))

    assert result == "Playing the playlist Discover Weekly."


def test_playlists_are_read_past_the_first_page() -> None:
    first = [
        {"type": "playlist", "uri": f"spotify:playlist:{i}", "name": f"P{i}"} for i in range(50)
    ]

    def pages(request: httpx.Request) -> dict[str, object]:
        if request.url.params["offset"] == "0":
            return {"items": first, "next": "https://api.spotify.com/v1/me/playlists?offset=50"}
        return {"items": [DISCOVER], "next": None}

    spotify = FakeSpotify({"GET /v1/me/playlists": pages})

    result = run(spotify.tools().spotify_play("Discover Weekly", mine=True))

    assert result == "Playing the playlist Discover Weekly."
    assert spotify.calls("GET").count("GET /me/playlists") == 2
    listed = run(spotify.tools().spotify_my_playlists())
    assert listed.endswith("P49; Discover Weekly.")


def test_playlist_paging_stops_at_the_cap() -> None:
    def endless(request: httpx.Request) -> dict[str, object]:
        return {"items": [DISCOVER], "next": "more"}

    spotify = FakeSpotify({"GET /v1/me/playlists": endless})

    run(spotify.tools().spotify_my_playlists())

    assert spotify.calls("GET").count("GET /me/playlists") == 6  # 300 / 50


def test_an_ambiguous_device_asks_which() -> None:
    tv = {"id": "dev-tv", "name": "Living Room TV", "type": "TV"}
    speaker = {"id": "dev-lr", "name": "Living Room Speaker", "type": "Speaker"}
    spotify = FakeSpotify({"GET /v1/me/player/devices": {"devices": [tv, speaker]}})

    result = run(spotify.tools().spotify_play("Dreams", device="living room"))

    # "speaker" is a filler word, so "Living Room Speaker" normalises to "living room" exactly.
    assert result == "Playing Dreams by Fleetwood Mac on Living Room Speaker."
    result = run(spotify.tools().spotify_volume(30, device="living"))
    assert result == "Did you mean Living Room TV or Living Room Speaker?"


def test_a_targeted_play_lists_devices_once(spotify: FakeSpotify) -> None:
    run(spotify.tools().spotify_play("Dreams", device="kitchen"))

    assert spotify.calls("GET").count("GET /me/player/devices") == 1
