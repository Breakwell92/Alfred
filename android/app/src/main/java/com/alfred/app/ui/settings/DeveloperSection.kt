package com.alfred.app.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.alfred.app.data.voice.Endpoint
import com.alfred.app.data.voice.vad.EndpointRecord
import com.alfred.app.data.voice.vad.EndpointReport
import com.alfred.app.data.voice.vad.TurnMode
import com.alfred.app.data.voice.vad.VadConfig
import com.alfred.app.data.voice.vad.VadSettings
import com.alfred.app.ui.theme.AlfredTheme
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.roundToInt
import org.koin.compose.viewmodel.koinViewModel

@Composable
fun DeveloperSection(viewModel: VadSettingsViewModel = koinViewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    DeveloperContent(
        state = state,
        onUseSileroChange = viewModel::setUseSilero,
        onThresholdChange = viewModel::setThreshold,
        onEndSilenceChange = viewModel::setEndSilence,
        onNoSpeechChange = viewModel::setNoSpeech,
        onMaxUtteranceChange = viewModel::setMaxUtterance,
        onMinSpeechChange = viewModel::setMinSpeech,
        onReset = viewModel::reset,
    )
}

/** End-of-speech tuning (ADR 0023): shown once the Settings title has been tapped 7 times. */
@Composable
fun DeveloperContent(
    state: VadUiState,
    onUseSileroChange: (Boolean) -> Unit,
    onThresholdChange: (Float) -> Unit,
    onEndSilenceChange: (Int) -> Unit,
    onNoSpeechChange: (Int) -> Unit,
    onMaxUtteranceChange: (Int) -> Unit,
    onMinSpeechChange: (Int) -> Unit,
    onReset: () -> Unit,
) {
    val config = state.settings.config
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Developer: end of speech", style = MaterialTheme.typography.titleMedium)
        Text(state.engine, style = MaterialTheme.typography.bodyMedium)
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            Text("Use Silero VAD", modifier = Modifier.weight(1f))
            Switch(checked = state.settings.useSilero, onCheckedChange = onUseSileroChange, enabled = state.loaded)
        }
        TuningSlider(
            label = "Speech threshold",
            value = config.threshold,
            range = VadSettings.THRESHOLD_RANGE,
            format = { "%.2f".format(Locale.ROOT, it) },
            enabled = state.loaded,
            onChange = onThresholdChange,
        )
        MillisSlider(
            "Silence that ends a turn",
            config.endSilenceMillis,
            VadSettings.END_SILENCE_RANGE,
            state.loaded,
            onEndSilenceChange,
        )
        MillisSlider(
            "Give up if nobody speaks",
            config.noSpeechMillis,
            VadSettings.NO_SPEECH_RANGE,
            state.loaded,
            onNoSpeechChange,
        )
        MillisSlider(
            "Longest wake word turn",
            config.maxUtteranceMillis,
            VadSettings.MAX_UTTERANCE_RANGE,
            state.loaded,
            onMaxUtteranceChange,
        )
        MillisSlider(
            "Speech needed to start",
            config.minSpeechMillis,
            VadSettings.MIN_SPEECH_RANGE,
            state.loaded,
            onMinSpeechChange,
        )
        OutlinedButton(onClick = onReset, enabled = state.loaded) { Text("Reset to defaults") }

        Text("Recent turns", style = MaterialTheme.typography.labelLarge)
        if (state.recent.isEmpty()) {
            Text(
                "None yet (Silero turns are logged to files/endpoint_events.jsonl)",
                style = MaterialTheme.typography.bodySmall,
            )
        }
        state.recent.forEach { record ->
            Text(describe(record), style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun MillisSlider(label: String, value: Int, range: IntRange, enabled: Boolean, onChange: (Int) -> Unit) {
    TuningSlider(
        label = label,
        value = value.toFloat(),
        range = range.first.toFloat()..range.last.toFloat(),
        format = { "${it.roundToInt()} ms" },
        enabled = enabled,
        onChange = { onChange(it.roundToInt()) },
    )
}

@Composable
private fun TuningSlider(
    label: String,
    value: Float,
    range: ClosedFloatingPointRange<Float>,
    format: (Float) -> String,
    enabled: Boolean,
    onChange: (Float) -> Unit,
) {
    // Saved when the drag ends, not on every step.
    var current by remember(value) { mutableFloatStateOf(value) }
    Column {
        Row(modifier = Modifier.fillMaxWidth()) {
            Text(label, style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f))
            Text(format(current), style = MaterialTheme.typography.labelLarge)
        }
        Slider(
            value = current,
            onValueChange = { current = it },
            onValueChangeFinished = { onChange(current) },
            valueRange = range,
            enabled = enabled,
        )
    }
}

/** E.g. "18:02:11 · wake word · silence · 3.4 s, ended 736 ms after speech · 310 µs/chunk". */
internal fun describe(record: EndpointRecord): String {
    val report = record.report
    val time = SimpleDateFormat("HH:mm:ss", Locale.ROOT).format(Date(record.atMillis))
    val mode = if (record.mode == TurnMode.HandsFree) "wake word" else "push-to-talk"
    val total = "%.1f s".format(Locale.ROOT, report.totalMillis / 1000f)
    val latency = report.latencyMillis?.let { ", ended $it ms after speech" }.orEmpty()
    val reason = report.reason.replace('_', ' ')
    return "$time · $mode · $reason · $total$latency · ${report.meanInferenceMicros} µs/chunk"
}

@Preview(showBackground = true)
@Composable
private fun DeveloperContentPreview() {
    AlfredTheme {
        DeveloperContent(
            state = VadUiState(
                loaded = true,
                engine = "Silero VAD",
                recent = listOf(
                    EndpointRecord(
                        atMillis = 0,
                        mode = TurnMode.HandsFree,
                        report = EndpointReport(Endpoint.End, "silence", 3_392, 704, 2_656, 736, 310),
                        config = VadConfig(),
                    ),
                ),
            ),
            onUseSileroChange = {},
            onThresholdChange = {},
            onEndSilenceChange = {},
            onNoSpeechChange = {},
            onMaxUtteranceChange = {},
            onMinSpeechChange = {},
            onReset = {},
        )
    }
}
