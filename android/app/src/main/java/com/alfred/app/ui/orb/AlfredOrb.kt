package com.alfred.app.ui.orb

import android.animation.ValueAnimator
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import com.alfred.app.ui.theme.AlfredTheme
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * The orb's sizes, measured from the design at 2 px per dp. The composable is a square of
 * [diameter]; the channel label may draw a little outside it.
 */
enum class OrbSize(
    internal val ringRadius: Dp,
    internal val tickLength: Dp,
    internal val majorTickLength: Dp,
    internal val coreRadius: Dp,
    /** The dashed inner circle. */
    internal val guideRadius: Dp,
) {
    /** Home. */
    Large(ringRadius = 125.dp, tickLength = 8.dp, majorTickLength = 10.dp, coreRadius = 34.dp, guideRadius = 77.dp),

    /**
     * Transmitting. Its core is bigger than [Large]'s on purpose: the design's speaking orb has
     * a large core (88 px radius at 2 px per dp) inside a smaller ring.
     */
    Small(ringRadius = 90.dp, tickLength = 6.dp, majorTickLength = 8.dp, coreRadius = 42.dp, guideRadius = 55.dp),
    ;

    /** Room for the ring, the longest tick and how far a lit tick grows. */
    val diameter: Dp get() = (ringRadius + majorTickLength + TICK_GROWTH) * 2
}

/**
 * Alfred's radar orb (STU-113, ADR 0020): tick ring, rotating sweep and glowing core, with
 * optional guides (dashed circle and crosshair) and a small [channelLabel] by the ring.
 *
 * [level] is 0 to 1: the mic level while [OrbState.Listening], the playback level while
 * [OrbState.Speaking]; it's ignored otherwise. With the system's "remove animations" setting
 * on, nothing moves and only the core's brightness shows the state and level.
 */
@Composable
fun AlfredOrb(
    state: OrbState,
    modifier: Modifier = Modifier,
    level: Float = 0f,
    size: OrbSize = OrbSize.Large,
    showGuides: Boolean = size == OrbSize.Large,
    channelLabel: String? = null,
) {
    val colors = AlfredTheme.colors
    val labelStyle = AlfredTheme.typography.label.copy(color = colors.textSecondary)
    val measurer = rememberTextMeasurer()
    val label = remember(channelLabel, labelStyle) { channelLabel?.let { measurer.measure(it, labelStyle) } }

    val motion = remember { OrbMotion(state, level) }
    val currentState by rememberUpdatedState(state)
    val currentLevel by rememberUpdatedState(level)
    // Read only in the draw phase, so a new frame redraws without recomposing.
    var frame by remember { mutableLongStateOf(0L) }
    // "Remove animations" can be switched while the orb stays composed (Home keeps it for
    // long stretches), so it's read again every time the screen resumes.
    var animate by remember { mutableStateOf(ValueAnimator.areAnimatorsEnabled()) }
    LifecycleResumeEffect(Unit) {
        animate = ValueAnimator.areAnimatorsEnabled()
        onPauseOrDispose {}
    }

    LaunchedEffect(animate) {
        if (!animate) {
            snapshotFlow { currentState to currentLevel }.collect { (s, l) ->
                motion.settle(s, l)
                frame++
            }
        } else {
            var last = withFrameNanos { it }
            while (true) {
                withFrameNanos { now ->
                    motion.advance((now - last) / 1_000_000_000f, currentState, currentLevel)
                    last = now
                    frame = now
                }
            }
        }
    }

    val dims = size
    Spacer(
        modifier
            .size(dims.diameter)
            .drawWithCache {
                // Everything that doesn't change frame to frame is built here, once per size.
                val c = Offset(this.size.width / 2f, this.size.height / 2f)
                val ring = dims.ringRadius.toPx()
                val tick = dims.tickLength.toPx()
                val majorTick = dims.majorTickLength.toPx()
                val growth = TICK_GROWTH.toPx()
                val core = dims.coreRadius.toPx()
                val guide = dims.guideRadius.toPx()
                val hairline = Stroke(1.dp.toPx())
                val tickWidth = 1.5.dp.toPx()
                val dashed = Stroke(
                    width = 1.dp.toPx(),
                    pathEffect = PathEffect.dashPathEffect(floatArrayOf(4.dp.toPx(), 4.dp.toPx())),
                )
                val rim = Stroke(2.dp.toPx())
                val haze = Brush.radialGradient(
                    0f to colors.accentDim.copy(alpha = 0.55f),
                    1f to colors.accentDim.copy(alpha = 0f),
                    center = c,
                    radius = ring * 0.8f,
                )
                // Brightest at the leading edge (0°), fading over the quarter turn behind it.
                val sweep = Brush.sweepGradient(
                    0f to colors.accent.copy(alpha = 0.38f),
                    0.25f to colors.accent.copy(alpha = 0f),
                    1f to colors.accent.copy(alpha = 0f),
                    center = c,
                )
                val glow = Brush.radialGradient(
                    0f to colors.accent.copy(alpha = 0.35f),
                    1f to colors.accent.copy(alpha = 0f),
                    center = c,
                    radius = core * 1.9f,
                )
                val coreFill = Brush.radialGradient(
                    0f to colors.accentGlow,
                    0.4f to colors.accent,
                    0.85f to colors.accent.copy(alpha = 0.5f),
                    1f to colors.accentDim,
                    center = c,
                    radius = core,
                )
                val rimColor = colors.background.copy(alpha = 0.55f)
                val edgeColor = colors.accent.copy(alpha = 0.6f)
                val labelAt = label?.let {
                    val r = ring + majorTick + 6.dp.toPx()
                    Offset(
                        c.x + r * cos(LABEL_ANGLE),
                        c.y + r * sin(LABEL_ANGLE) - it.size.height / 2f,
                    )
                }

                onDrawBehind {
                    frame // redraw on every animation frame

                    drawCircle(haze, ring, c)
                    rotate(motion.sweepAngle, c) {
                        drawCircle(sweep, ring, c)
                        drawLine(edgeColor, c, Offset(c.x + ring, c.y), tickWidth)
                    }
                    if (showGuides) {
                        drawCircle(colors.outline, guide, c, style = dashed)
                        drawLine(colors.outline, Offset(c.x - ring, c.y), Offset(c.x + ring, c.y), hairline.width)
                        drawLine(colors.outline, Offset(c.x, c.y - ring), Offset(c.x, c.y + ring), hairline.width)
                    }
                    drawCircle(colors.outline, ring, c, style = hairline)

                    for (i in 0 until TICKS) {
                        val lit = motion.tickHighlight(i * TICK_STEP)
                        val length = (if (i % MAJOR_EVERY == 0) majorTick else tick) + growth * lit
                        val dx = TICK_COS[i]
                        val dy = TICK_SIN[i]
                        drawLine(
                            lerp(colors.textDim, colors.accent, lit),
                            Offset(c.x + dx * ring, c.y + dy * ring),
                            Offset(c.x + dx * (ring + length), c.y + dy * (ring + length)),
                            tickWidth,
                        )
                    }

                    val brightness = motion.coreBrightness
                    scale(motion.coreScale, c) {
                        drawCircle(glow, core * 1.9f, c, alpha = brightness)
                        drawCircle(coreFill, core, c, alpha = brightness)
                        drawCircle(rimColor, core, c, style = rim)
                    }

                    if (label != null && labelAt != null) drawText(label, topLeft = labelAt)
                }
            },
    )
}

/** How much longer a fully lit tick gets. */
private val TICK_GROWTH = 4.dp

private const val TICKS = 72
private const val TICK_STEP = 360f / TICKS

/** Every 18th tick (12, 3, 6 and 9 o'clock) is longer. */
private const val MAJOR_EVERY = TICKS / 4

/** Up and to the right of the ring, where the design puts "CH-01". */
private const val LABEL_ANGLE = (-40.0 * PI / 180.0).toFloat()

private val TICK_COS = FloatArray(TICKS) { cos(it * TICK_STEP * PI / 180.0).toFloat() }
private val TICK_SIN = FloatArray(TICKS) { sin(it * TICK_STEP * PI / 180.0).toFloat() }
