package com.alfred.app.ui.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.Card
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.alfred.app.data.server.ConnectionStatus
import com.alfred.app.ui.theme.AlfredTheme
import org.koin.compose.viewmodel.koinViewModel

@Composable
fun HomeScreen(viewModel: HomeViewModel = koinViewModel()) {
    val status by viewModel.status.collectAsStateWithLifecycle()
    HomeContent(status = status, onCheckNow = viewModel::refresh)
}

@Composable
fun HomeContent(status: ConnectionStatus, onCheckNow: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        StatusCard(status = status, onCheckNow = onCheckNow)
        Spacer(Modifier.weight(1f))
        // Talk button area: push-to-talk arrives with the voice loop (STU-65).
        FilledIconButton(
            onClick = {},
            enabled = false,
            shape = CircleShape,
            modifier = Modifier.size(120.dp),
        ) {
            Icon(Icons.Filled.Mic, contentDescription = "Talk", modifier = Modifier.size(48.dp))
        }
        Text(
            text = "Voice coming soon",
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.padding(top = 12.dp),
        )
        Spacer(Modifier.weight(1f))
    }
}

@Composable
private fun StatusCard(status: ConnectionStatus, onCheckNow: () -> Unit) {
    val view = status.toView()
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(view.icon, contentDescription = null)
                Spacer(Modifier.width(12.dp))
                Text(view.title, style = MaterialTheme.typography.titleLarge)
            }
            view.detail?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
            OutlinedButton(onClick = onCheckNow) { Text("Check now") }
        }
    }
}

private data class StatusView(val title: String, val detail: String?, val icon: ImageVector)

private fun ConnectionStatus.toView(): StatusView = when (this) {
    ConnectionStatus.Checking -> StatusView("Checking…", null, Icons.Filled.Sync)
    ConnectionStatus.Connected -> StatusView("Connected", null, Icons.Filled.CheckCircle)
    ConnectionStatus.Unauthorised ->
        StatusView("Unauthorised", "The server rejected the API token.", Icons.Filled.Lock)
    ConnectionStatus.InvalidToken ->
        StatusView(
            "Invalid token",
            "The API token has characters that can't be sent. Re-enter it in Settings.",
            Icons.Filled.Lock,
        )
    ConnectionStatus.NotConfigured ->
        StatusView("Not set up", "Enter the server URL and API token in Settings.", Icons.Filled.Settings)
    is ConnectionStatus.Unreachable ->
        StatusView("Unreachable", "Is Tailscale on and the server running? ($reason)", Icons.Filled.CloudOff)
    is ConnectionStatus.ServerError ->
        StatusView("Server error", "The server answered $code.", Icons.Filled.Error)
}

@Preview(showBackground = true)
@Composable
private fun HomeContentPreview() {
    AlfredTheme { HomeContent(status = ConnectionStatus.Connected, onCheckNow = {}) }
}
