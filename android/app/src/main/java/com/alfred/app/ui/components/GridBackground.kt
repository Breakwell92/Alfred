package com.alfred.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.material3.LocalContentColor
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.unit.Dp
import com.alfred.app.ui.theme.AlfredTheme

/**
 * The screen background: near-black with a faint square grid, drawn once behind [content].
 * It also sets the default text and icon colour, so plain `Text` on it isn't black.
 */
@Composable
fun GridBackground(
    modifier: Modifier = Modifier,
    cell: Dp = AlfredTheme.spacing.gridCell,
    content: @Composable BoxScope.() -> Unit,
) {
    val colors = AlfredTheme.colors
    Box(
        modifier = modifier
            .background(colors.background)
            .drawBehind {
                val step = cell.toPx()
                if (step <= 0f) return@drawBehind
                var x = 0f
                while (x <= size.width) {
                    drawLine(colors.gridLine, Offset(x, 0f), Offset(x, size.height))
                    x += step
                }
                var y = 0f
                while (y <= size.height) {
                    drawLine(colors.gridLine, Offset(0f, y), Offset(size.width, y))
                    y += step
                }
            },
    ) {
        CompositionLocalProvider(LocalContentColor provides colors.textPrimary) { content() }
    }
}
