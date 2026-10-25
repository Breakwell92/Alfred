package com.alfred.app.ui.theme

import androidx.compose.material3.ColorScheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Immutable
import androidx.compose.ui.graphics.Color

/**
 * Alfred's palette (ADR 0018). Dark only, one accent. These are the only colour literals in the
 * app: everything else reads them through [AlfredTheme.colors] or [MaterialTheme.colorScheme].
 */
@Immutable
data class AlfredColors(
    val background: Color,
    /** Panels, tiles, cards and icon buttons. */
    val surface: Color,
    /** Something sitting on a [surface], e.g. the play button on a persona card. */
    val surfaceRaised: Color,
    val surfacePressed: Color,
    /** The faint square grid behind every screen. */
    val gridLine: Color,
    /** Hairline borders and the orb's rings. */
    val outline: Color,
    val textPrimary: Color,
    /** Labels, descriptions, telemetry. */
    val textSecondary: Color,
    /** Words not spoken or confirmed yet. */
    val textDim: Color,
    val accent: Color,
    val accentPressed: Color,
    /** The orb's sweep and glow falloff. */
    val accentDim: Color,
    /** The near-white centre of the orb's glowing core. */
    val accentGlow: Color,
    /** Text and icons on an [accent] fill. */
    val onAccent: Color,
    /** The REC dot and errors. */
    val record: Color,
)

val AlfredDarkColors = AlfredColors(
    background = Color(0xFF07080A),
    surface = Color(0xFF111418),
    surfaceRaised = Color(0xFF181C21),
    surfacePressed = Color(0xFF1D2228),
    gridLine = Color(0x0AFFFFFF),
    outline = Color(0xFF2A2F36),
    textPrimary = Color(0xFFE8EAED),
    textSecondary = Color(0xFF8A9099),
    textDim = Color(0xFF4F555D),
    accent = Color(0xFF5AC8FA),
    accentPressed = Color(0xFF45A9D6),
    accentDim = Color(0xFF1F4A5E),
    accentGlow = Color(0xFFE9F8FF),
    onAccent = Color(0xFF07080A),
    record = Color(0xFFC0463E),
)

/** Material 3 colours from Alfred's, so stock Material components fit in without overrides. */
fun AlfredColors.toColorScheme(): ColorScheme = darkColorScheme(
    primary = accent,
    onPrimary = onAccent,
    primaryContainer = accentDim,
    onPrimaryContainer = textPrimary,
    inversePrimary = accentDim,
    secondary = textSecondary,
    onSecondary = background,
    secondaryContainer = surfaceRaised,
    onSecondaryContainer = accent,
    tertiary = accent,
    onTertiary = onAccent,
    tertiaryContainer = accentDim,
    onTertiaryContainer = textPrimary,
    background = background,
    onBackground = textPrimary,
    surface = background,
    onSurface = textPrimary,
    surfaceVariant = surface,
    onSurfaceVariant = textSecondary,
    surfaceTint = accent,
    inverseSurface = textPrimary,
    inverseOnSurface = background,
    error = record,
    onError = textPrimary,
    errorContainer = record,
    onErrorContainer = textPrimary,
    outline = outline,
    outlineVariant = outline,
    scrim = background,
    surfaceBright = surfaceRaised,
    surfaceDim = background,
    surfaceContainer = surface,
    surfaceContainerHigh = surfaceRaised,
    surfaceContainerHighest = surfaceRaised,
    surfaceContainerLow = surface,
    surfaceContainerLowest = background,
)
