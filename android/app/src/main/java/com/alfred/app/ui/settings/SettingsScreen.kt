package com.alfred.app.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.alfred.app.ui.components.GridBackground
import com.alfred.app.ui.theme.AlfredTheme
import org.koin.compose.viewmodel.koinViewModel

@Composable
fun SettingsScreen(viewModel: SettingsViewModel = koinViewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    SettingsContent(
        state = state,
        onServerUrlChange = viewModel::onServerUrlChange,
        onApiTokenChange = viewModel::onApiTokenChange,
        onSave = viewModel::save,
        onPairingCodeChange = viewModel::onPairingCodeChange,
        onPair = viewModel::pair,
        wakeWord = { WakeWordSection() },
    )
}

@Composable
fun SettingsContent(
    state: SettingsUiState,
    onServerUrlChange: (String) -> Unit,
    onApiTokenChange: (String) -> Unit,
    onSave: () -> Unit,
    onPairingCodeChange: (String) -> Unit,
    onPair: () -> Unit,
    wakeWord: @Composable () -> Unit = {},
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("Settings", style = MaterialTheme.typography.headlineSmall)
        OutlinedTextField(
            value = state.serverUrl,
            onValueChange = onServerUrlChange,
            label = { Text("Server URL") },
            supportingText = { Text("Tailscale name of the home server, e.g. http://glados:8000") },
            singleLine = true,
            enabled = state.loaded,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = state.apiToken,
            onValueChange = onApiTokenChange,
            label = { Text("API token") },
            supportingText = {
                Text(state.tokenError ?: "Same value as ALFRED_API_TOKEN on the server")
            },
            isError = state.tokenError != null,
            singleLine = true,
            enabled = state.loaded,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth(),
        )
        Button(onClick = onSave, enabled = state.loaded) { Text("Save") }
        if (state.saved) {
            Text("Saved", style = MaterialTheme.typography.bodyMedium)
        }

        HorizontalDivider()
        Text("Approvals", style = MaterialTheme.typography.titleMedium)
        Text(
            state.pairedDeviceName?.let { "Paired as $it. This app can approve irreversible actions." }
                ?: "Not paired. Irreversible actions (like unlocking a door) can only be approved " +
                "from a paired app.",
            style = MaterialTheme.typography.bodyMedium,
        )
        OutlinedTextField(
            value = state.pairingCode,
            onValueChange = onPairingCodeChange,
            label = { Text("Pairing code") },
            supportingText = {
                Text(
                    state.pairingError
                        ?: "On the server: python -m alfred_server.devices pair --name <phone>",
                )
            },
            isError = state.pairingError != null,
            singleLine = true,
            enabled = state.loaded && !state.pairing,
            keyboardOptions = KeyboardOptions(
                capitalization = KeyboardCapitalization.Characters,
                autoCorrectEnabled = false,
            ),
            modifier = Modifier.fillMaxWidth(),
        )
        Button(onClick = onPair, enabled = state.loaded && !state.pairing) {
            Text(if (state.pairedDeviceName == null) "Pair" else "Pair again")
        }

        HorizontalDivider()
        wakeWord()
    }
}

@Preview
@Composable
private fun SettingsContentPreview() {
    AlfredTheme {
        GridBackground {
            SettingsContent(
                state = SettingsUiState(serverUrl = "http://glados:8000", apiToken = "x", loaded = true),
                onServerUrlChange = {},
                onApiTokenChange = {},
                onSave = {},
                onPairingCodeChange = {},
                onPair = {},
            )
        }
    }
}
