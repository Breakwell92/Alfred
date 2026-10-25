package com.alfred.app.ui.settings

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.selectable
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.alfred.app.data.wakeword.StockWakeWord
import com.alfred.app.data.wakeword.WakeCounts
import com.alfred.app.data.wakeword.WakeWordSettings
import com.alfred.app.ui.theme.AlfredTheme
import org.koin.compose.viewmodel.koinViewModel

@Composable
fun WakeWordSection(viewModel: WakeWordSettingsViewModel = koinViewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    var permissionDenied by remember { mutableStateOf(false) }
    val requestPermissions = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { results ->
        // The notification permission is nice to have; the mic is required.
        val mic = results[Manifest.permission.RECORD_AUDIO] ?: hasMic(context)
        permissionDenied = !mic
        if (mic) viewModel.setEnabled(true)
    }
    WakeWordContent(
        state = state,
        permissionDenied = permissionDenied,
        onEnabledChange = { enabled ->
            if (!enabled) {
                viewModel.setEnabled(false)
            } else {
                val wanted = buildList {
                    if (!hasMic(context)) add(Manifest.permission.RECORD_AUDIO)
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
                        PackageManager.PERMISSION_GRANTED
                    ) {
                        add(Manifest.permission.POST_NOTIFICATIONS)
                    }
                }
                if (wanted.isEmpty()) viewModel.setEnabled(true) else requestPermissions.launch(wanted.toTypedArray())
            }
        },
        onSensitivityChange = viewModel::setSensitivity,
        onChimeChange = viewModel::setChime,
        onModelChange = viewModel::setModel,
        onPauseInDndChange = viewModel::setPauseInDoNotDisturb,
    )
}

private fun hasMic(context: Context) =
    ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

@Composable
fun WakeWordContent(
    state: WakeWordUiState,
    permissionDenied: Boolean,
    onEnabledChange: (Boolean) -> Unit,
    onSensitivityChange: (Float) -> Unit,
    onChimeChange: (Boolean) -> Unit,
    onModelChange: (StockWakeWord) -> Unit,
    onPauseInDndChange: (Boolean) -> Unit,
) {
    val settings = state.settings
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Wake word", style = MaterialTheme.typography.titleMedium)
        SwitchRow("Listen for the wake word", settings.enabled, state.loaded, onEnabledChange)
        Text(
            when {
                permissionDenied -> "Alfred needs microphone permission to listen for the wake word"
                settings.enabled -> state.status
                else -> "Off. When on, Alfred listens on the phone (nothing is sent until it hears the wake word)."
            },
            style = MaterialTheme.typography.bodyMedium,
            color = if (permissionDenied) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
        )

        Text("Wake word", style = MaterialTheme.typography.labelLarge)
        StockWakeWord.entries.forEach { model ->
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .fillMaxWidth()
                    .selectable(
                        selected = settings.model == model,
                        enabled = state.loaded,
                        role = Role.RadioButton,
                        onClick = { onModelChange(model) },
                    ),
            ) {
                RadioButton(selected = settings.model == model, onClick = null, enabled = state.loaded)
                Text(model.label, modifier = Modifier.padding(start = 8.dp))
            }
        }

        // Saved when the drag ends, not on every step.
        var sensitivity by remember(settings.sensitivity) { mutableFloatStateOf(settings.sensitivity) }
        Text("Sensitivity", style = MaterialTheme.typography.labelLarge)
        Slider(
            value = sensitivity,
            onValueChange = { sensitivity = it },
            onValueChangeFinished = { onSensitivityChange(sensitivity) },
            enabled = state.loaded,
        )
        Text(
            "Higher wakes more easily but more often by mistake",
            style = MaterialTheme.typography.bodySmall,
        )

        SwitchRow("Chime when it hears you", settings.chime, state.loaded, onChimeChange)
        SwitchRow("Pause while Do Not Disturb is on", settings.pauseInDoNotDisturb, state.loaded, onPauseInDndChange)
        Text(
            "Heard ${state.counts.detections} times, ${state.counts.falseAccepts} reported as " +
                "“That wasn't me”",
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@Composable
private fun SwitchRow(label: String, checked: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Text(label, modifier = Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onChange, enabled = enabled)
    }
}

@Preview(showBackground = true)
@Composable
private fun WakeWordContentPreview() {
    AlfredTheme {
        WakeWordContent(
            state = WakeWordUiState(
                settings = WakeWordSettings(enabled = true),
                loaded = true,
                status = "Listening for “Hey Jarvis”",
                counts = WakeCounts(detections = 12, falseAccepts = 1),
            ),
            permissionDenied = false,
            onEnabledChange = {},
            onSensitivityChange = {},
            onChimeChange = {},
            onModelChange = {},
            onPauseInDndChange = {},
        )
    }
}
