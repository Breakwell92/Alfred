package com.alfred.app.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.runtime.Immutable
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.alfred.app.R

/** Condensed display face: wordmark, headings, buttons, tile values. */
val DisplayFont = FontFamily(
    Font(R.font.rajdhani_medium, FontWeight.Medium),
    Font(R.font.rajdhani_semibold, FontWeight.SemiBold),
    Font(R.font.rajdhani_bold, FontWeight.Bold),
)

/** Monospace for status lines, labels and telemetry. The bundled file is a variable font. */
val MonoFont = FontFamily(
    monoFont(FontWeight.Normal),
    monoFont(FontWeight.Medium),
    monoFont(FontWeight.Bold),
)

/** Humanist sans for transcript, reply and description text. */
val BodyFont = FontFamily(
    Font(R.font.titillium_web_regular, FontWeight.Normal),
    Font(R.font.titillium_web_semibold, FontWeight.SemiBold),
)

private fun monoFont(weight: FontWeight) = Font(
    R.font.jetbrains_mono,
    weight,
    variationSettings = FontVariation.Settings(FontVariation.weight(weight.weight)),
)

/**
 * Alfred's text styles (sizes measured from the design at 2 px per dp). Labels are meant to be
 * upper case; components upper-case their own text, since a [TextStyle] can't.
 */
@Immutable
data class AlfredTypography(
    /** "A L F R E D". */
    val wordmark: TextStyle = TextStyle(
        fontFamily = DisplayFont,
        fontWeight = FontWeight.Bold,
        fontSize = 24.sp,
        letterSpacing = 0.4.em,
    ),
    /** "WHAT DO YOU NEED?". */
    val displayL: TextStyle = TextStyle(
        fontFamily = DisplayFont,
        fontWeight = FontWeight.SemiBold,
        fontSize = 38.sp,
        lineHeight = 42.sp,
        letterSpacing = 0.01.em,
    ),
    /** Screen titles, e.g. "HOW SHALL I SOUND, SIR?". */
    val displayM: TextStyle = TextStyle(
        fontFamily = DisplayFont,
        fontWeight = FontWeight.Bold,
        fontSize = 32.sp,
        lineHeight = 36.sp,
    ),
    /** Card titles and tile values, e.g. "CLASSIC". */
    val title: TextStyle = TextStyle(
        fontFamily = DisplayFont,
        fontWeight = FontWeight.Bold,
        fontSize = 20.sp,
        lineHeight = 24.sp,
        letterSpacing = 0.08.em,
    ),
    /** Mono labels and status lines, e.g. "SECURE LINE · ENCRYPTED". */
    val label: TextStyle = TextStyle(
        fontFamily = MonoFont,
        fontWeight = FontWeight.Normal,
        fontSize = 11.sp,
        lineHeight = 14.sp,
        letterSpacing = 0.15.em,
    ),
    /** Transcript and reply text. */
    val body: TextStyle = TextStyle(
        fontFamily = BodyFont,
        fontWeight = FontWeight.Normal,
        fontSize = 22.sp,
        lineHeight = 30.sp,
    ),
    /** Descriptions under card titles and hints. */
    val bodySmall: TextStyle = TextStyle(
        fontFamily = BodyFont,
        fontWeight = FontWeight.Normal,
        fontSize = 15.sp,
        lineHeight = 20.sp,
    ),
    val button: TextStyle = TextStyle(
        fontFamily = DisplayFont,
        fontWeight = FontWeight.Bold,
        fontSize = 17.sp,
        lineHeight = 20.sp,
        letterSpacing = 0.2.em,
    ),
)

/** Material 3's type scale on Alfred's fonts, for stock components and the older screens. */
fun AlfredTypography.toMaterial(): Typography = Typography(
    displayLarge = displayL.copy(fontSize = 44.sp, lineHeight = 48.sp),
    displayMedium = displayL,
    displaySmall = displayM,
    headlineLarge = displayM,
    headlineMedium = displayM.copy(fontSize = 28.sp, lineHeight = 32.sp),
    headlineSmall = displayM.copy(fontSize = 24.sp, lineHeight = 28.sp),
    titleLarge = title.copy(fontSize = 22.sp, lineHeight = 26.sp),
    titleMedium = title.copy(fontSize = 18.sp, lineHeight = 22.sp),
    titleSmall = title.copy(fontSize = 16.sp, lineHeight = 20.sp),
    bodyLarge = bodySmall.copy(fontSize = 17.sp, lineHeight = 24.sp),
    bodyMedium = bodySmall,
    bodySmall = bodySmall.copy(fontSize = 13.sp, lineHeight = 18.sp),
    labelLarge = button.copy(fontSize = 15.sp, letterSpacing = 0.12.em),
    labelMedium = label.copy(fontSize = 12.sp, lineHeight = 16.sp),
    labelSmall = label,
)
