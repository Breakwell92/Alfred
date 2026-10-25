package com.alfred.app.ui.home

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.HourglassTop
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.alfred.app.data.server.ConnectionStatus
import com.alfred.app.data.voice.LinkState
import com.alfred.app.data.voice.PendingConfirmation
import com.alfred.app.data.voice.TurnMetadata
import com.alfred.app.data.voice.VoicePhase
import com.alfred.app.data.voice.VoiceState
import com.alfred.app.ui.components.GridBackground
import com.alfred.app.ui.theme.AlfredTheme
import com.alfred.app.ui.theme.OctagonShape
import java.util.Locale
import org.koin.compose.viewmodel.koinViewModel

@Composable
fun HomeScreen(
    viewModel: HomeViewModel = koinViewModel(),
    voiceViewModel: VoiceViewModel = koinViewModel(),
) {
    val status by viewModel.status.collectAsStateWithLifecycle()
    val voice by voiceViewModel.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    var micDenied by rememberSaveable { mutableStateOf(false) }
    val askForMic = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        micDenied = !granted
    }

    HomeContent(
        status = status,
        voice = voice,
        micDenied = micDenied,
        onCheckNow = viewModel::refresh,
        onTalkPressed = {
            // Checked on every press, so a grant made in system settings is picked up.
            if (hasMic(context)) {
                micDenied = false
                voiceViewModel.onTalkPressed()
                true
            } else {
                askForMic.launch(Manifest.permission.RECORD_AUDIO)
                false
            }
        },
        onTalkReleased = voiceViewModel::onTalkReleased,
        onOpenAppSettings = { openAppSettings(context) },
        onAnswer = voiceViewModel::answer,
        onDismissConfirmation = voiceViewModel::dismissConfirmation,
    )
}

private fun hasMic(context: Context): Boolean =
    ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
        PackageManager.PERMISSION_GRANTED

private fun openAppSettings(context: Context) {
    val uri = Uri.fromParts("package", context.packageName, null)
    context.startActivity(
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
    )
}

/**
 * [onTalkPressed] returns whether the press was taken (false while asking for the mic
 * permission); only then is [onTalkReleased] called with how long it was held.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeContent(
    status: ConnectionStatus,
    voice: VoiceUiState,
    micDenied: Boolean,
    onCheckNow: () -> Unit,
    onTalkPressed: () -> Boolean,
    onTalkReleased: (heldMillis: Long) -> Unit,
    onOpenAppSettings: () -> Unit,
    onAnswer: (approve: Boolean) -> Unit,
    onDismissConfirmation: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        StatusCard(status = status, link = voice.link, onCheckNow = onCheckNow)
        Conversation(
            voice = voice.voice,
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        )
        if (micDenied) {
            Text(
                "Alfred needs the microphone to hear you.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.error,
            )
            OutlinedButton(onClick = onOpenAppSettings) { Text("Open app settings") }
        }
        TalkButton(phase = voice.voice.phase, onPressed = onTalkPressed, onReleased = onTalkReleased)
        Text(talkHint(voice.voice.phase), style = MaterialTheme.typography.bodyMedium)
    }

    voice.voice.confirmation?.let { confirmation ->
        ModalBottomSheet(onDismissRequest = onDismissConfirmation) {
            ConfirmationSheet(
                confirmation = confirmation,
                error = voice.voice.error,
                onAnswer = onAnswer,
            )
        }
    }
}

@Composable
private fun StatusCard(status: ConnectionStatus, link: LinkState, onCheckNow: () -> Unit) {
    val view = status.toView()
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(view.icon, contentDescription = null)
                Spacer(Modifier.width(12.dp))
                Text(view.title, style = MaterialTheme.typography.titleLarge)
            }
            view.detail?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
            Text("Voice: ${link.describe()}", style = MaterialTheme.typography.bodyMedium)
            OutlinedButton(onClick = onCheckNow) { Text("Check now") }
        }
    }
}

@Composable
private fun Conversation(voice: VoiceState, modifier: Modifier = Modifier) {
    var showDetails by rememberSaveable { mutableStateOf(false) }
    Column(
        modifier = modifier.verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (voice.transcript.isNotEmpty()) {
            Text("You", style = MaterialTheme.typography.labelMedium)
            Text(voice.transcript, style = MaterialTheme.typography.bodyLarge)
        }
        if (voice.reply.isNotEmpty()) {
            Text("Alfred", style = MaterialTheme.typography.labelMedium)
            Text(voice.reply, style = MaterialTheme.typography.bodyLarge)
        }
        voice.metadata?.let { metadata ->
            TextButton(onClick = { showDetails = !showDetails }) {
                Text(if (showDetails) "Hide details" else "Details")
            }
            if (showDetails) {
                metadata.describe().forEach { Text(it, style = MaterialTheme.typography.bodySmall) }
            }
        }
        // While the confirmation sheet is up, errors about the answer show there instead.
        if (voice.confirmation == null) {
            voice.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        }
    }
}

@Composable
private fun TalkButton(
    phase: VoicePhase,
    onPressed: () -> Boolean,
    onReleased: (Long) -> Unit,
) {
    val pressed by rememberUpdatedState(onPressed)
    val released by rememberUpdatedState(onReleased)
    val label = talkLabel(phase)
    val listening = phase == VoicePhase.Listening
    Surface(
        shape = OctagonShape,
        color = if (listening) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
        contentColor = if (listening) MaterialTheme.colorScheme.onError else MaterialTheme.colorScheme.onPrimary,
        modifier = Modifier
            .size(140.dp)
            .semantics {
                role = Role.Button
                contentDescription = label
                onClick {
                    // TalkBack's double tap acts as a tap (toggle mode).
                    if (pressed()) released(0)
                    true
                }
            }
            .pointerInput(Unit) {
                detectTapGestures(
                    onPress = {
                        val start = System.currentTimeMillis()
                        if (pressed()) {
                            tryAwaitRelease()
                            released(System.currentTimeMillis() - start)
                        }
                    },
                )
            },
    ) {
        Box(contentAlignment = Alignment.Center) {
            Icon(talkIcon(phase), contentDescription = null, modifier = Modifier.size(56.dp))
        }
    }
}

@Composable
private fun ConfirmationSheet(
    confirmation: PendingConfirmation,
    error: String?,
    onAnswer: (Boolean) -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 24.dp)
            .padding(bottom = 32.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            if (confirmation.irreversible) "Approve this? It can't be undone." else "Go ahead?",
            style = MaterialTheme.typography.titleLarge,
        )
        Text(confirmation.summary, style = MaterialTheme.typography.bodyLarge)
        if (!confirmation.irreversible) {
            Text("You can also just say yes or no.", style = MaterialTheme.typography.bodySmall)
        }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedButton(
                onClick = { onAnswer(false) },
                enabled = !confirmation.answering,
                modifier = Modifier.weight(1f),
            ) { Text("Deny") }
            Button(
                onClick = { onAnswer(true) },
                enabled = !confirmation.answering,
                modifier = Modifier.weight(1f),
            ) { Text("Approve") }
        }
        Spacer(Modifier.height(8.dp))
    }
}

private fun talkIcon(phase: VoicePhase): ImageVector = when (phase) {
    VoicePhase.Idle -> Icons.Filled.Mic
    VoicePhase.Listening -> Icons.AutoMirrored.Filled.Send
    VoicePhase.Thinking -> Icons.Filled.HourglassTop
    VoicePhase.Speaking -> Icons.Filled.Stop
}

private fun talkLabel(phase: VoicePhase): String = when (phase) {
    VoicePhase.Idle -> "Talk"
    VoicePhase.Listening -> "Send"
    VoicePhase.Thinking, VoicePhase.Speaking -> "Stop"
}

private fun talkHint(phase: VoicePhase): String = when (phase) {
    VoicePhase.Idle -> "Hold to talk, or tap to start"
    VoicePhase.Listening -> "Listening… let go or tap to send"
    VoicePhase.Thinking -> "Thinking… tap to stop"
    VoicePhase.Speaking -> "Speaking… tap to stop"
}

private fun LinkState.describe(): String = when (this) {
    LinkState.Offline -> "offline"
    LinkState.NotConfigured -> "not set up"
    LinkState.Connecting -> "connecting…"
    is LinkState.Connected -> if (voice) "connected" else "connected, but voice is off on the server"
    is LinkState.Waiting -> "reconnecting in ${(retryInMillis + 999) / 1000} s ($reason)"
    is LinkState.Rejected -> reason
}

/** One line each: routing, tools, tokens, cost, timing. */
private fun TurnMetadata.describe(): List<String> = buildList {
    listOfNotNull(specialist, alias, tier).takeIf { it.isNotEmpty() }?.let { add(it.joinToString(" · ")) }
    reason?.let { add("Why: $it") }
    if (tools.isNotEmpty()) add("Tools: ${tools.joinToString()}")
    if (inputTokens != null || outputTokens != null) {
        add("Tokens: ${inputTokens ?: 0} in, ${outputTokens ?: 0} out")
    }
    costGbp?.let { add("Cost: £" + String.format(Locale.UK, "%.4f", it)) }
    timings["first_audio_ms"]?.let { add("First audio: ${it.toLong()} ms after you stopped") }
    timings["total_ms"]?.let { add("Total: ${it.toLong()} ms") }
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

@Preview
@Composable
private fun HomeContentPreview() {
    AlfredTheme {
        GridBackground {
            HomeContent(
                status = ConnectionStatus.Connected,
                voice = VoiceUiState(
                    voice = VoiceState(transcript = "What time is it?", reply = "It's ten past three."),
                    link = LinkState.Connected("s1", voice = true),
                ),
                micDenied = false,
                onCheckNow = {},
                onTalkPressed = { true },
                onTalkReleased = {},
                onOpenAppSettings = {},
                onAnswer = {},
                onDismissConfirmation = {},
            )
        }
    }
}
