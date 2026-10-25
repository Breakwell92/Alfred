# 0022: Home screen

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-114

## Context

Screen 1 of the app design (attached to STU-111) is Alfred at rest. Top to bottom it has:

- a header: menu button, the "ALFRED" wordmark with an accent subtitle, and a crosshair button
- a status line ("SECURE LINE · ENCRYPTED" on the left, place and time on the right)
- the large radar orb (ADR 0020)
- a greeting by time of day and "WHAT DO YOU NEED?"
- a bottom row: a PROTOCOL tile (the active persona), the octagon mic button and a PATROL tile

It replaces the scaffold's Home from STU-51 (status card, conversation, talk button) and the bottom navigation bar, which the design doesn't have.

## Decision

**Layout.** `HomeContent` is stateless and built from the design system (ADR 0018). The design's artboards are 1170 × 2532 px, so 3 px per dp on a 390 dp-wide screen. Measured from them:

- header buttons 44 dp, `DiagonalCorners`
- tiles 104 dp wide, PROTOCOL cut top-start/bottom-end and PATROL mirrored
- mic octagon 96 dp
- `spacing.gutter` sides

Tiles grow if a persona name is longer. The mic sits in a `Box` so it stays centred when they do. Tile values are single-line (`StatTile` now never wraps a value). `AlfredOrb(OrbSize.Large)` shrinks with a `graphicsLayer` scale when the screen is too short for its 278 dp, so a 360 × 640 dp phone still shows the whole screen. On such a phone the headline wraps to two lines.

**Status line** (`statusLine(ConnectionStatus, LinkState)` in `HomeState.kt`). It combines the HTTP check, polled every 10 s by `HomeViewModel`, with the device socket's `LinkState`, which notices a drop at once:

| HTTP check | Voice socket | Line | Colour |
| --- | --- | --- | --- |
| Connected | Connected | SECURE LINE · ENCRYPTED | accent |
| Connected | connecting / waiting / offline | RECONNECTING… | amber (`AlfredColors.warning`, new) |
| Checking | not connected | CONNECTING… | amber |
| Unreachable / server error, or socket refused | any | LINE DOWN | red |
| Unauthorised / invalid token | any | UNAUTHORISED | red |
| Not configured | any | NOT SET UP | red |

"Encrypted" is true: the app reaches the server over Tailscale (WireGuard). The clock is 24-hour and ticks on each minute boundary.

**Greeting.** `greetingFor(hour)`:

- 05:00–11:59 → morning
- 12:00–17:59 → afternoon
- 18:00–04:59 → evening (the design greets "GOOD EVENING" at 02:14)

**Flavour copy** is in `res/values/strings.xml`: subtitle "Batcave Uplink", default name "Master Bruce", place "Gotham" and persona "Classic". Name and place are settings (Settings → Home screen, `ProfileSettings` in the settings DataStore under their own keys). Blank means the default.

**Persona.** The PROTOCOL tile shows the active persona's name. `HomeViewModel` asks `GET /personas/active` each time the server comes (back) up and caches the answer in `ProfileSettings.personaName`, so the tile keeps the last name offline. STU-66 builds the endpoint. **It must answer `200 {"name": "<display name>", ...}`**. Until it exists the call gets a 404 and the tile shows the cached name or "Classic". Tapping the tile opens Voice Protocol, a placeholder destination until STU-118.

**Mic button.** `OctagonActionButton` (through `ShapedButton`) gained:

- `onPress`: follows the finger like the old talk button. Tap toggles, holding is hold-to-talk (`VoiceViewModel`, ADR 0021), and accessibility's click is a tap.
- `onDisabledClick`

With no voice-enabled link, the idle mic is disabled. Tapping it toasts "Not connected to Alfred" and retries the socket at once, as pressing Talk did before. Mid-turn it always works (send / stop). The Recording layer is unchanged.

**Thinking and speaking.** The orb follows the voice phase: idle, listening, thinking, speaking, with the mic or playback level. Until the Transmitting screen (STU-116) exists, the greeting gives way to the transcript and reply while Alfred thinks and speaks. The crosshair opens a sheet with the connection detail (the old status card, with "Check now") and the last turn: transcript, reply, and the routing/tools/tokens/cost/timing lines that used to sit under "Details". Patrol (STU-119) may take the crosshair over later. The PATROL tile reads OFF and does nothing until STU-119 defines it.

**Navigation.** The bottom bar is gone. Home's menu button opens a `ModalNavigationDrawer`:

- Home
- Voice Protocol
- Connectors
- Activity
- Settings

Every destination sits directly above Home, so back always returns there. The drawer opens only from the button; a swipe can close it.

## Consequences

- Screenshot tests `HomeScreenshotTest` cover idle-connected, offline, and idle on a 360 × 640 dp screen (Roborazzi goldens in `android/app/src/test/screenshots/`). The orb never stops animating, so the test drives Compose's clock by hand. It runs with a plain `Application`, because `AlfredApplication` starts Koin once per JVM and a second Robolectric test in the same class would fail.
- Android lint's `ConfigurationScreenWidthHeight` detector crashes when a local variable reads `OrbSize.Large.diameter`, so `HomeScreen.kt` keeps it in a top-level `ORB_SIDE`.
- Other screens have no menu button of their own; back to Home, then the menu. That's fine while Home is the hub. Revisit if a screen gets deep.
- The status line can show RECONNECTING… for up to 10 s before LINE DOWN when the server dies: the socket notices at once, the HTTP poll on its next run.
