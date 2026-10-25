package com.alfred.app.ui.recording

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.unit.dp
import com.alfred.app.ui.theme.AlfredTheme

/**
 * The live mic waveform: symmetric bars around a centre baseline, newest on the right, the
 * oldest fading to accent-dim. [bars] is read while drawing, so a new reading redraws the canvas
 * without recomposing anything.
 */
@Composable
fun Waveform(bars: () -> List<Float>, modifier: Modifier = Modifier) {
    val colors = AlfredTheme.colors
    Canvas(modifier.background(colors.surface)) {
        val hairline = 1.dp.toPx()
        val midY = size.height / 2
        drawLine(colors.outline, Offset(0f, 0f), Offset(size.width, 0f), hairline)
        drawLine(colors.outline, Offset(0f, size.height - hairline), Offset(size.width, size.height - hairline), hairline)
        drawLine(colors.outline, Offset(0f, midY), Offset(size.width, midY), hairline)

        val levels = bars()
        if (levels.isEmpty()) return@Canvas
        val pitch = size.width / levels.size
        val barWidth = pitch * 0.5f
        val minHeight = 2.dp.toPx()
        val maxHeight = size.height - 8.dp.toPx()
        levels.forEachIndexed { i, level ->
            val height = (level.coerceIn(0f, 1f) * maxHeight).coerceAtLeast(minHeight)
            val color = lerp(colors.accentDim, colors.accent, barBrightness(i, levels.size))
            drawRect(
                color = color,
                topLeft = Offset(i * pitch + (pitch - barWidth) / 2, midY - height / 2),
                size = Size(barWidth, height),
            )
        }
    }
}

/** 0 (accent-dim) for the oldest bars rising to 1 (accent) over the left third: the fading tail. */
internal fun barBrightness(index: Int, count: Int): Float {
    if (count <= 1) return 1f
    val position = index.toFloat() / (count - 1)
    return (position / TAIL_FRACTION).coerceIn(0f, 1f)
}

private const val TAIL_FRACTION = 0.35f
