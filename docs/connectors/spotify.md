# Spotify

Connector id `spotify` (`server/alfred_server/connectors/builtin/spotify.py`), used by the `media` specialist. Design: [ADR 0014](../decisions/0014-spotify-media.md).

Playback control needs **Spotify Premium**. Search, playlists and "what's playing" work without it.

## 1. Create a Spotify developer app

1. Sign in at <https://developer.spotify.com/dashboard> with Dan's Spotify account and **Create app**.
2. Name and description: anything (e.g. "Alfred"). API: tick **Web API**.
3. **Redirect URI**: `<ALFRED_PUBLIC_URL>/connectors/spotify/auth/callback`. Spotify only accepts HTTPS URLs or a loopback address, not `http://glados:8000`. Either:
   - HTTPS through Tailscale: `tailscale serve --bg 8000` on the server, then use `https://glados.<tailnet>.ts.net/connectors/spotify/auth/callback` and set `ALFRED_PUBLIC_URL=https://glados.<tailnet>.ts.net`; or
   - loopback, signing in from a browser on the server itself: `http://127.0.0.1:8000/connectors/spotify/auth/callback` with `ALFRED_PUBLIC_URL=http://127.0.0.1:8000`.
4. Save. In the app's **User Management**, add Dan's account (apps start in development mode, open only to listed users).
5. Copy the **Client ID** and **Client secret**.

## 2. Connect it

```bash
TOKEN=...   # ALFRED_API_TOKEN
curl -X PUT -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"enabled": true, "oauth_client": {"client_id": "...", "client_secret": "..."}}' \
  $ALFRED_PUBLIC_URL/connectors/spotify
curl -H "Authorization: Bearer $TOKEN" $ALFRED_PUBLIC_URL/connectors/spotify/auth/start
```

Open the returned `url` in a browser, sign in and accept. The callback page says "connected", and `GET /connectors` shows `spotify` as `connected`. Tokens refresh in the background.

Scopes requested: `user-read-playback-state`, `user-modify-playback-state`, `user-read-currently-playing`, `playlist-read-private`, `user-library-read`, `user-library-modify`.

## Tools

| Tool | Risk | Asks first? | What |
| --- | --- | --- | --- |
| `spotify_now_playing` | read | no | Track, artist, album and device ("what's this song?") |
| `spotify_devices` | read | no | Devices that can play now |
| `spotify_search` | read | no | Search tracks, artists, albums or playlists |
| `spotify_my_playlists` | read | no | Dan's playlists |
| `spotify_play` | write | no (default tier `read`) | Play a query (own playlists first, then search) on an optional device; no query resumes |
| `spotify_pause`, `spotify_next`, `spotify_previous` | write | no | Playback |
| `spotify_volume`, `spotify_shuffle`, `spotify_repeat` | write | no | Settings |
| `spotify_transfer` | write | no | Move playback to another device |
| `spotify_queue` | write | no | Queue a track |
| `spotify_like_current` | write | no | Save the current track to Liked Songs |

The controls are honestly `write` but run without asking (ADR 0014). To make one ask first:

```bash
curl -X PUT -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"risk": "write"}' $ALFRED_PUBLIC_URL/settings/permissions/spotify/spotify_play
```

## Devices

Spotify only lists a device while it can play: the Spotify app open on a phone or computer, or a Spotify Connect speaker that's awake. Say devices as you like ("the kitchen speaker", "kitchen"): names are matched loosely. If nothing is listed, Alfred says "No Spotify device is active. Open Spotify on your phone or a speaker and try again." Rename devices in their own apps to make them easy to say.

## Known limits

- **Premium.** Without it every playback command answers "Spotify only allows playback control with a Premium account."
- **Spotify's own playlists.** Since late 2024 Spotify hides its algorithmic and editorial playlists (Discover Weekly, Release Radar, Daily Mixes) from newer API apps. If Discover Weekly isn't in `spotify_my_playlists`, follow it or save a copy into your own playlist.
- Development-mode apps allow up to 25 listed users, which is plenty for Dan.
