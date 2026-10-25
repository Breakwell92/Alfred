package com.alfred.app.ui.recording

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.alfred.app.data.voice.MicMeter
import com.alfred.app.ui.components.ChamferIconButton
import com.alfred.app.ui.components.GridBackground
import com.alfred.app.ui.components.OctagonActionButton
import com.alfred.app.ui.components.StatusPill
import com.alfred.app.ui.theme.AlfredColors
import com.alfred.app.ui.theme.AlfredTheme
import java.util.Locale
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlinx.coroutines.delay

/**
 * Screen 2 of the design, shown over everything while a turn is being listened to (ADR 0021):
 * REC timer, live waveform and telemetry, and the streaming transcript.
 */
@Composable
fun RecordingScreen(viewModel: RecordingViewModel, modifier: Modifier = Modifier) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val meter = viewModel.meter.collectAsStateWithLifecycle()
    val bars = viewModel.bars.collectAsStateWithLifecycle()
    val elapsed by produceState(0L, state.sinceMillis) {
        val since = state.sinceMillis ?: return@produceState
        while (true) {
            value = (System.currentTimeMillis() - since).coerceAtLeast(0)
            delay(TIMER_TICK_MILLIS)
        }
    }
    BackHandler(onBack = viewModel::cancel)
    RecordingContent(
        elapsedMillis = elapsed,
        stable = state.stable,
        unstable = state.unstable,
        meter = { meter.value },
        bars = { bars.value },
        onCancel = viewModel::cancel,
        onStop = viewModel::stop,
        modifier = modifier,
    )
}

/**
 * Stateless Recording screen. [meter] and [bars] are read only where they're shown, so the
 * per-frame readings redraw the waveform and telemetry text without recomposing the rest.
 * [showVoiceprintMatch] waits for speaker verification (STU-96).
 */
@Composable
fun RecordingContent(
    elapsedMillis: Long,
    stable: String,
    unstable: String,
    meter: () -> MicMeter,
    bars: () -> List<Float>,
    onCancel: () -> Unit,
    onStop: () -> Unit,
    modifier: Modifier = Modifier,
    showVoiceprintMatch: Boolean = false,
    voiceprintName: String = "",
) {
    val colors = AlfredTheme.colors
    val type = AlfredTheme.typography
    val spacing = AlfredTheme.spacing
    GridBackground(
        modifier
            // A layer over Home: taps mustn't fall through to the screen underneath.
            .pointerInput(Unit) { awaitPointerEventScope { while (true) awaitPointerEvent() } },
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .windowInsetsPadding(WindowInsets.safeDrawing)
                .padding(horizontal = spacing.gutter, vertical = spacing.l),
            verticalArrangement = Arrangement.spacedBy(spacing.l),
        ) {
            Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                ChamferIconButton(
                    icon = Icons.Filled.Close,
                    contentDescription = "Cancel",
                    onClick = onCancel,
                    modifier = Modifier.align(Alignment.CenterStart),
                )
                StatusPill(label = "REC · ${formatElapsed(elapsedMillis)}", indicatorColor = colors.record)
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = spacing.s),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text("VOICEPRINT", style = type.label, color = colors.textSecondary)
                if (showVoiceprintMatch) {
                    Text("MATCH · ${voiceprintName.uppercase()}", style = type.label, color = colors.accent)
                } else {
                    Text("—", style = type.label, color = colors.textDim)
                }
            }

            Waveform(
                bars = bars,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(120.dp),
            )

            Telemetry(meter)

            LiveTranscript(
                stable = stable,
                unstable = unstable,
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f),
            )

            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(spacing.m),
            ) {
                OctagonActionButton(
                    icon = Icons.Filled.Stop,
                    contentDescription = "Send",
                    onClick = onStop,
                    outlined = true,
                )
                Text("TAP TO SEND · OR GO QUIET", style = type.label, color = colors.textSecondary)
                Spacer(Modifier.height(spacing.s))
            }
        }
    }
}

/** Its own composable, so a new reading recomposes only this line. */
@Composable
private fun Telemetry(meter: () -> MicMeter) {
    Text(
        telemetryLine(meter()),
        style = AlfredTheme.typography.label,
        color = AlfredTheme.colors.textSecondary,
    )
}

@Composable
private fun LiveTranscript(stable: String, unstable: String, modifier: Modifier = Modifier) {
    val colors = AlfredTheme.colors
    val style = AlfredTheme.typography.body
    val scroll = rememberScrollState()
    val text = transcriptText(stable, unstable, colors)
    // Newest words stay in view.
    LaunchedEffect(text) { scroll.animateScrollTo(scroll.maxValue) }
    val maxHeight = with(LocalDensity.current) { (style.lineHeight * MAX_TRANSCRIPT_LINES).toDp() }
    Box(modifier) {
        Text(
            text = text,
            style = style,
            modifier = Modifier
                .heightIn(max = maxHeight)
                .verticalScroll(scroll),
        )
    }
}

/** Confirmed words bright, the provisional tail dim; a dim prompt until anything's heard. */
internal fun transcriptText(stable: String, unstable: String, colors: AlfredColors): AnnotatedString =
    buildAnnotatedString {
        if (stable.isBlank() && unstable.isBlank()) {
            withStyle(SpanStyle(color = colors.textDim)) { append("Listening…") }
            return@buildAnnotatedString
        }
        withStyle(SpanStyle(color = colors.textPrimary)) { append(stable.trim()) }
        if (unstable.isNotBlank()) {
            if (stable.isNotBlank()) append(' ')
            withStyle(SpanStyle(color = colors.textDim)) { append(unstable.trim()) }
        }
    }

/** "mm:ss". */
internal fun formatElapsed(millis: Long): String {
    val seconds = millis.coerceAtLeast(0) / 1000
    return String.format(Locale.ROOT, "%02d:%02d", seconds / 60, seconds % 60)
}

/** "−32 dB · NOISE FILTER · ON · 16 kHz". */
internal fun telemetryLine(meter: MicMeter): String {
    val db = meter.dbfs.roundToInt()
    val level = if (db < 0) "−${-db} dB" else "$db dB"
    val filter = if (meter.noiseFilter) "ON" else "OFF"
    val khz = meter.sampleRate / 1000.0
    val rate = if (khz % 1.0 == 0.0) "${khz.toInt()} kHz" else String.format(Locale.ROOT, "%.1f kHz", khz)
    return "$level · NOISE FILTER · $filter · $rate"
}

private const val TIMER_TICK_MILLIS = 250L
private const val MAX_TRANSCRIPT_LINES = 5

@Preview(widthDp = 362, heightDp = 784)
@Composable
private fun RecordingContentPreview() {
    AlfredTheme {
        RecordingContent(
            elapsedMillis = 4_000,
            stable = "Alfred, what's the fastest way to",
            unstable = "the docks from here",
            meter = { MicMeter(dbfs = -32f, level = 0.45f, noiseFilter = true) },
            bars = { previewBars() },
            onCancel = {},
            onStop = {},
        )
    }
}

/** A plausible bit of speech for previews and the screenshot test. */
internal fun previewBars(count: Int = 48): List<Float> = List(count) { i ->
    val envelope = if (i < 6) 0.1f else 0.35f + 0.5f * abs(sin(i * 0.55f))
    (envelope * (0.6f + 0.4f * abs(cos(i * 1.7f)))).coerceIn(0f, 1f)
}
