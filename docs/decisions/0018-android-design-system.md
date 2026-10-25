# 0018: Android design system (theme, type, chamfered components)

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-112

## Context

Dan's app design (attached to STU-111: Home, Recording, Transmitting, Voice Protocol) uses one visual language on every screen: a near-black background with a faint square grid, a single light-blue accent, cut-corner (chamfered) panels and buttons, an octagonal main button, and three type faces (condensed display, monospace, body sans). The scaffold (STU-51) used stock Material 3 with dynamic wallpaper colours. The screen tickets (STU-113 to STU-118) need shared pieces so they only compose them.

## Decision

**Theme (`ui/theme/`).** `AlfredTheme { }` is dark only; there is no light theme and no dynamic colour. It provides Alfred's own tokens through `AlfredTheme.colors`, `.typography` and `.spacing` (static composition locals, the same pattern as `MaterialTheme`). It also maps them onto Material 3's `ColorScheme`, `Typography` and `Shapes`, so stock components (navigation bar, text fields, cards, sheets) fit in without per-call overrides.

- `Color.kt`: `AlfredColors` has background `#07080A`, surface `#111418`, surfaceRaised, surfacePressed, gridLine (4 % white), outline, textPrimary `#E8EAED`, textSecondary, textDim, accent `#5AC8FA`, accentPressed, accentDim, onAccent and record (REC red). Every value was sampled from the design PDF. **These are the only colour literals in the Kotlin code.** `ThemeTest` fails the build if `Color(0x…)`, `Color.White` and the like, or `"#RRGGBB"` appear outside `ui/theme/`. The one XML copy is `res/values/colors.xml` → `alfred_background`, the window background shown before Compose draws.
- `Type.kt`: the three bundled font families are in `res/font/`, all under the SIL OFL, with licence texts in `android/licenses/fonts/`.
  - **Rajdhani** (Medium, SemiBold, Bold) for the wordmark, headings, buttons and tile values. It matches the design's squared condensed caps.
  - **JetBrains Mono** (variable font, weights picked with `FontVariation`) for status lines, labels and telemetry.
  - **Titillium Web** (Regular, SemiBold) for transcript, reply and description text. The design's body text is close to Rajdhani, but Rajdhani is hard to read in long passages. Titillium Web is a humanist sans with the same technical feel, as the ticket asked for. Swapping it means changing `BodyFont` only.

  Text styles are `wordmark`, `displayL`, `displayM`, `title`, `label`, `body`, `bodySmall` and `button`. Sizes were measured at 2 px per dp from the 724 px artboards. Upper-casing is done by the components, since a `TextStyle` can't do it.
- `Spacing.kt`: a 4/8/12/16/24/32 scale, a 20 dp side `gutter` (41 px in the design) and a 24 dp `gridCell`.
- `Shapes.kt`:
  - `ChamferShape(cut, corners)` returns Compose's `CutCornerShape` with the chosen corners cut. It is RTL-aware (start and end corners), compares by value, and can be used in Material's `Shapes`.
  - `OctagonShape` is a `CutCornerShape` with a 29.3 % cut, which gives a regular octagon on a square.
  - `HexagonPillShape(point)` is a custom outline for the CUT IN pill. Its tips are steeper than a 45° cut, as in the design.
  - `DiagonalCorners` (top-start and bottom-end) is the default cut.

**Components (`ui/components/`).**
- `GridBackground` draws the grid behind content. `AlfredApp` puts it behind the whole scaffold, which is transparent, so screens draw no background of their own.
- `ShapedButton` is the internal base for every button. It clips the fill, border and ripple to the shape, changes the fill while pressed, dims and ignores taps when disabled, and sets `Role.Button`.
- On top of it: `ChamferIconButton` (any `corners`, `emptySet()` for a square), `OctagonActionButton` (filled, or `outlined` for the stop state), `PrimaryButton` and `HexagonButton`.
- `StatusPill`, `StatTile` (optionally clickable, accent value, any corners and alignment) and `SelectableCard` (a radio-role card with an accent rail when selected and a trailing slot).
- Every component has a `@Preview` in `DesignSystemPreviews.kt`.

**Existing screens.** Home, Settings, Connectors and Activity now use the new theme through the Material mapping. The talk button is an octagon. The status and navigation bars are dark with light icons whatever the system theme. The screens' layouts are unchanged; STU-114 to STU-118 redesign them.

## Consequences

- Screen tickets build from these components. A new colour goes into `AlfredColors`, never inline.
- No dynamic colour or light mode. If either is ever wanted, add another `AlfredColors` instance; components already read only tokens.
- The fonts add about 1.5 MB to the APK, mostly Rajdhani's Devanagari glyphs. Subsetting them is possible later if size matters.
- The ripple-clipping and pressed-state behaviour was checked in previews and on an emulator, not by automated UI tests: the project has no Compose UI test or screenshot test setup. Shape geometry and theme mapping are unit-tested.
