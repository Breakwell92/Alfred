# 0020: Radar orb (animated Canvas component)

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-113

## Context

The orb is Alfred's "face": large on Home (STU-114), smaller on the Transmitting screen (STU-116). It has to show at a glance whether Alfred is idle, listening, thinking or speaking. It reacts to the mic and playback level, and it runs all the time the screen is up, so it must hold 60 fps on a mid-range phone. The design (attached to STU-111) shows a tick ring, a rotating sweep wedge, a glowing core and, on Home, a dashed inner circle, a crosshair and a "CH-01" label.

## Decision

**API (`ui/orb/`).** `AlfredOrb(state, modifier, level, size, showGuides, channelLabel)`. `OrbSize.Large` (278 dp square, ring radius 125 dp) and `OrbSize.Small` (204 dp, ring 90 dp) were measured from the design at 2 px per dp. `showGuides` defaults to on for `Large` only. `level` is 0 to 1: the mic level while listening, the playback level while speaking. The screens take it from `VoiceController.level` (see Consequences).

**Animation model.** `OrbMotion` is plain Kotlin with no Compose, so it is unit-tested (`OrbMotionTest`).
- Each `OrbState` has a weight that eases between 0 and 1 (time constant 0.25 s). Every animated value is the weighted mix of the states' values, so a state change blends instead of cutting.
- The sweep angle is integrated from the blended speed: 6, 3, 1.2 and 2.5 s a turn for idle, listening, thinking and speaking. Changing state therefore never makes the angle jump.
- The sweep turns anticlockwise. In the design the glow sits clockwise of the sharp edge, so this keeps the sharp edge leading and the glow trailing, as the ticket describes.
- `level` is smoothed with a fast attack (40 ms) and a slower release (180 ms), so the core follows syllables without flicker.
- A long gap between frames (the app was in the background) counts as one 100 ms step.
- Animation time loops every 12 s, a whole number of cycles of every effect, so float precision holds however long the orb runs.
- What each state does:
  - **Idle:** core breathes (±4 % over 4 s).
  - **Listening:** core grows up to +35 % and brightens with the level.
  - **Thinking:** steady 1.25 Hz pulse; a bright band runs clockwise round the ticks over a flicker.
  - **Speaking:** core grows with the level, and the ticks by the sweep's edge light up and lengthen with it.

**Drawing.** One `Spacer` with `drawWithCache`, which is a `Canvas` whose setup is cached. Everything fixed is built once per size in the cache block: strokes, the dash effect, the radial and sweep gradient brushes, the measured label, and the tick sin/cos tables. The per-frame draw only rotates and scales the canvas and draws lines and circles with value-class colours and offsets, so nothing is allocated per frame. Frames come from a `withFrameNanos` loop that updates `OrbMotion` and a frame counter read only in the draw phase. A frame therefore redraws without recomposing. The core's near-white centre is a new token, `AlfredColors.accentGlow`.

**Reduced motion.** When animator duration scale is 0 (`ValueAnimator.areAnimatorsEnabled()` is false, the system's "remove animations"), the frame loop doesn't run. `OrbMotion.settle` snaps to the state each time the state or level changes. The sweep stays put, the core keeps its size, the ticks don't light, and only the core's brightness shows the state and level. The settled brightnesses sit well apart: idle 0.45, listening and speaking 0.6 to 0.85 with the level, thinking 1. The setting is read again every time the screen resumes (`LifecycleResumeEffect`).

**Debug screen.** `src/debug/.../OrbDebugActivity` is in debug builds only and has no launcher icon. Start it with `adb shell am start -n com.alfred.app/com.alfred.app.debug.OrbDebugActivity`. It drives the orb with `FakeSpeech` (syllables with a quick rise and a slower fall, grouped into phrases with pauses) and switches state, size and guides. `ThemeTest`'s colour-literal check now covers `src/debug/` as well.

## Consequences

- Measured on a Galaxy S25 (SM-S931B, 120 Hz) running the debug screen with `dumpsys gfxinfo`, about 8 s per state. The p99 frame time was 11 ms in every state, with 0 or 1 janky frames out of about 975 and a GPU p99 of 7 ms or less. A mid-range phone is still to be checked (STU-113, "Needs Dan to verify").
- The level source is `VoiceController.level` (also `VoiceViewModel.level`), added in STU-125; see [ADR 0017](0017-android-voice-client.md), "Levels". Screens pass it straight to `level`: the orb does its own smoothing, so the source isn't smoothed.
- The channel label is drawn outside the orb's square to the upper right. Give the orb room there if a parent clips.
- `OrbSize.Small` has a bigger core (42 dp) than `Large` (34 dp) on purpose. That matches the design's Transmitting orb.
