package com.alfred.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf

private val LocalAlfredColors = staticCompositionLocalOf { AlfredDarkColors }
private val LocalAlfredTypography = staticCompositionLocalOf { AlfredTypography() }
private val LocalAlfredSpacing = staticCompositionLocalOf { AlfredSpacing() }

/**
 * Alfred's dark-only theme (ADR 0018). Provides Alfred's own tokens through [AlfredTheme] and
 * the matching Material 3 colours, type and shapes for stock components.
 */
@Composable
fun AlfredTheme(content: @Composable () -> Unit) {
    val colors = AlfredDarkColors
    val typography = AlfredTypography()
    CompositionLocalProvider(
        LocalAlfredColors provides colors,
        LocalAlfredTypography provides typography,
        LocalAlfredSpacing provides AlfredSpacing(),
    ) {
        MaterialTheme(
            colorScheme = colors.toColorScheme(),
            typography = typography.toMaterial(),
            shapes = AlfredMaterialShapes,
            content = content,
        )
    }
}

object AlfredTheme {
    val colors: AlfredColors
        @Composable @ReadOnlyComposable
        get() = LocalAlfredColors.current

    val typography: AlfredTypography
        @Composable @ReadOnlyComposable
        get() = LocalAlfredTypography.current

    val spacing: AlfredSpacing
        @Composable @ReadOnlyComposable
        get() = LocalAlfredSpacing.current
}
