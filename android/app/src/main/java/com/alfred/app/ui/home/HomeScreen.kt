package com.alfred.app.ui.home

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.GpsNotFixed
import androidx.compose.material.icons.filled.HourglassTop
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.alfred.app.R
import com.alfred.app.data.server.ConnectionStatus
import com.alfred.app.data.settings.ProfileSettings
import com.alfred.app.data.voice.LinkState
import com.alfred.app.data.voice.PendingConfirmation
import com.alfred.app.data.voice.TurnMetadata
import com.alfred.app.data.voice.VoicePhase
import com.alfred.app.data.voice.VoiceState
import com.alfred.app.ui.components.ChamferIconButton
import com.alfred.app.ui.components.GridBackground
import com.alfred.app.ui.components.OctagonActionButton
import com.alfred.app.ui.components.StatTile
import com.alfred.app.ui.orb.AlfredOrb
import com.alfred.app.ui.orb.OrbSize
import com.alfred.app.ui.theme.AlfredTheme
import com.alfred.app.ui.theme.Corner
import java.time.LocalTime
import java.util.Locale
import kotlin.math.min
import kotlinx.coroutines.delay
import org.koin.compose.viewmodel.koinViewModel

/** Screen 1 of the design (STU-114, ADR 0022): Alfred waiting to be asked. */
@Composable
fun HomeScreen(
    onOpenMenu: () -> Unit,
    onOpenProtocol: () -> Unit,
    viewModel: HomeViewModel = koinViewModel(),
    voiceViewModel: VoiceViewModel = koinViewModel(),
) {
    val status by viewModel.status.collectAsStateWithLifecycle()
    val profile by viewModel.profile.collectAsStateWithLifecycle()
    val voice by voiceViewModel.state.collectAsStateWithLifecycle()
    val level = voiceViewModel.level.collectAsStateWithLifecycle()
    val context = LocalContext.current
    var micDenied by rememberSaveable { mutableStateOf(false) }
    val askForMic = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        micDenied = !granted
    }
    // Ticks on each minute boundary rather than every 60 s from whenever Home appeared.
    val time by produceState(LocalTime.now()) {
        while (true) {
            val now = LocalTime.now()
            value = now
            delay((60 - now.second) * 1000L - now.nano / 1_000_000)
        }
    }

    HomeContent(
        status = status,
        voice = voice,
        level = { level.value },
        profile = profile,
        time = time,
        micDenied = micDenied,
        onOpenMenu = onOpenMenu,
        onOpenProtocol = onOpenProtocol,
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
        onMicUnavailable = {
            Toast.makeText(context, R.string.mic_not_connected, Toast.LENGTH_SHORT).show()
            voiceViewModel.reconnect()
        },
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
 * Stateless Home. [onTalkPressed] returns whether the press was taken (false while asking for
 * the mic permission); only then is [onTalkReleased] called with how long it was held. [level]
 * is read only by the orb. [time] drives both the clock and the greeting.
 *
 * Until the Transmitting screen (STU-116) exists, the greeting gives way to the exchange while
 * Alfred thinks and speaks; the crosshair's sheet keeps the last turn and the connection detail.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeContent(
    status: ConnectionStatus,
    voice: VoiceUiState,
    level: () -> Float,
    profile: ProfileSettings,
    time: LocalTime,
    micDenied: Boolean,
    onOpenMenu: () -> Unit,
    onOpenProtocol: () -> Unit,
    onCheckNow: () -> Unit,
    onTalkPressed: () -> Boolean,
    onTalkReleased: (heldMillis: Long) -> Unit,
    onMicUnavailable: () -> Unit,
    onOpenAppSettings: () -> Unit,
    onAnswer: (approve: Boolean) -> Unit,
    onDismissConfirmation: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val spacing = AlfredTheme.spacing
    var showDetail by rememberSaveable { mutableStateOf(false) }
    val phase = voice.voice.phase

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = spacing.gutter)
            .padding(top = spacing.m, bottom = spacing.xl),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Header(onOpenMenu = onOpenMenu, onOpenDetail = { showDetail = true })
        Spacer(Modifier.height(spacing.xl))
        StatusRow(
            line = statusLine(status, voice.link),
            place = profile.location ?: stringResource(R.string.default_location),
            time = time,
        )
        Orb(
            phase = phase,
            level = level,
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        )
        if (phase == VoicePhase.Thinking || phase == VoicePhase.Speaking) {
            Exchange(voice.voice)
        } else {
            Greeting(
                name = profile.name ?: stringResource(R.string.default_name),
                hour = time.hour,
                // While the confirmation sheet is up, errors about the answer show there instead.
                error = voice.voice.error.takeIf { voice.voice.confirmation == null },
            )
        }
        if (micDenied) {
            MicDenied(onOpenAppSettings)
        }
        Spacer(Modifier.weight(0.3f))
        BottomRow(
            persona = profile.personaName ?: stringResource(R.string.default_persona),
            phase = phase,
            micEnabled = micEnabled(phase, voice.link),
            onOpenProtocol = onOpenProtocol,
            onTalkPressed = onTalkPressed,
            onTalkReleased = onTalkReleased,
            onMicUnavailable = onMicUnavailable,
        )
    }

    if (showDetail) {
        ModalBottomSheet(onDismissRequest = { showDetail = false }) {
            DetailSheet(status = status, voice = voice, onCheckNow = onCheckNow)
        }
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
private fun Header(onOpenMenu: () -> Unit, onOpenDetail: () -> Unit) {
    val colors = AlfredTheme.colors
    val type = AlfredTheme.typography
    Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
        ChamferIconButton(
            icon = Icons.Filled.Menu,
            contentDescription = "Menu",
            onClick = onOpenMenu,
            size = HEADER_BUTTON,
            modifier = Modifier.align(Alignment.CenterStart),
        )
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            // Letter spacing trails the last letter too; pad the start so the word sits centred.
            Text(
                "ALFRED",
                style = type.wordmark,
                color = colors.textPrimary,
                modifier = Modifier.padding(start = 9.dp),
            )
            Text(
                stringResource(R.string.home_subtitle).uppercase(),
                style = type.label,
                color = colors.accent,
            )
        }
        ChamferIconButton(
            icon = Icons.Filled.GpsNotFixed,
            contentDescription = "Status",
            onClick = onOpenDetail,
            size = HEADER_BUTTON,
            modifier = Modifier.align(Alignment.CenterEnd),
        )
    }
}

@Composable
private fun StatusRow(line: StatusLine, place: String, time: LocalTime) {
    val colors = AlfredTheme.colors
    val type = AlfredTheme.typography
    val (indicator, text) = when (line.tone) {
        StatusTone.Secure -> colors.accent to colors.textSecondary
        StatusTone.Pending -> colors.warning to colors.warning
        StatusTone.Down -> colors.record to colors.record
    }
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier
                .size(7.dp)
                .background(indicator),
        )
        Text(
            stringResource(line.label).uppercase(),
            style = type.label,
            color = text,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(start = 10.dp, end = AlfredTheme.spacing.s),
        )
        // Weighted, so it's measured after the connection state and gets only the room left:
        // a long place set in Settings is ellipsised rather than squeezing the state.
        Text(
            place.uppercase(),
            style = type.label,
            color = colors.textSecondary,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            textAlign = TextAlign.End,
            modifier = Modifier.weight(1f),
        )
        Text(
            " ${formatClock(time)}",
            style = type.label,
            color = colors.textSecondary,
            maxLines = 1,
        )
    }
}

/** The large orb, shrunk to fit when the screen is short (it never stretches). */
@Composable
private fun Orb(phase: VoicePhase, level: () -> Float, modifier: Modifier = Modifier) {
    BoxWithConstraints(modifier, contentAlignment = Alignment.Center) {
        val scale = min(1f, min(maxWidth / ORB_SIDE, maxHeight / ORB_SIDE))
        AlfredOrb(
            state = orbStateFor(phase),
            level = level(),
            size = OrbSize.Large,
            showGuides = true,
            channelLabel = "CH-01",
            modifier = Modifier
                .requiredSize(ORB_SIDE)
                .graphicsLayer {
                    scaleX = scale
                    scaleY = scale
                },
        )
    }
}

@Composable
private fun Greeting(name: String, hour: Int, error: String?) {
    val colors = AlfredTheme.colors
    val type = AlfredTheme.typography
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            stringResource(greetingFor(hour), name).uppercase(),
            style = type.label.copy(fontSize = type.label.fontSize * GREETING_SCALE),
            color = colors.accent,
            textAlign = TextAlign.Center,
        )
        Text(
            stringResource(R.string.home_prompt).uppercase(),
            style = type.displayL,
            color = colors.textPrimary,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = AlfredTheme.spacing.xs),
        )
        error?.let {
            Text(
                it,
                style = type.bodySmall,
                color = colors.record,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(top = AlfredTheme.spacing.s),
            )
        }
    }
}

/** What was asked and what Alfred is saying, while he thinks and speaks. */
@Composable
private fun Exchange(voice: VoiceState) {
    val colors = AlfredTheme.colors
    val type = AlfredTheme.typography
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(AlfredTheme.spacing.s),
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(max = 200.dp)
            .verticalScroll(rememberScrollState()),
    ) {
        if (voice.transcript.isNotEmpty()) {
            Text(voice.transcript, style = type.bodySmall, color = colors.textSecondary, textAlign = TextAlign.Center)
        }
        Text(
            voice.reply.ifEmpty { "…" },
            style = type.body,
            color = colors.textPrimary,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun MicDenied(onOpenAppSettings: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(top = AlfredTheme.spacing.m)) {
        Text(
            "Alfred needs the microphone to hear you.",
            style = AlfredTheme.typography.bodySmall,
            color = AlfredTheme.colors.record,
        )
        TextButton(onClick = onOpenAppSettings) { Text("Open app settings") }
    }
}

@Composable
private fun BottomRow(
    persona: String,
    phase: VoicePhase,
    micEnabled: Boolean,
    onOpenProtocol: () -> Unit,
    onTalkPressed: () -> Boolean,
    onTalkReleased: (Long) -> Unit,
    onMicUnavailable: () -> Unit,
) {
    // A Box, not a Row, so the mic stays centred when a long persona name widens its tile.
    Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
        StatTile(
            label = "Protocol",
            value = persona,
            corners = setOf(Corner.TopStart, Corner.BottomEnd),
            onClick = onOpenProtocol,
            modifier = Modifier
                .align(Alignment.CenterStart)
                .widthIn(min = TILE_WIDTH),
        )
        OctagonActionButton(
            icon = talkIcon(phase),
            contentDescription = talkLabel(phase),
            // What TalkBack's double tap does: a tap (toggle mode).
            onClick = { if (onTalkPressed()) onTalkReleased(0) },
            onPress = {
                val start = System.currentTimeMillis()
                if (onTalkPressed()) {
                    tryAwaitRelease()
                    onTalkReleased(System.currentTimeMillis() - start)
                }
            },
            enabled = micEnabled,
            onDisabledClick = onMicUnavailable,
            size = MIC_BUTTON,
        )
        // Patrol mode is defined by STU-119; until then it's always off.
        StatTile(
            label = "Patrol",
            value = "Off",
            corners = setOf(Corner.TopEnd, Corner.BottomStart),
            modifier = Modifier
                .align(Alignment.CenterEnd)
                .widthIn(min = TILE_WIDTH),
        )
    }
}

/** The crosshair's sheet: the connection in words, and how the last turn ran. */
@Composable
private fun DetailSheet(status: ConnectionStatus, voice: VoiceUiState, onCheckNow: () -> Unit) {
    val colors = AlfredTheme.colors
    val type = AlfredTheme.typography
    val view = status.toView()
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = AlfredTheme.spacing.xl)
            .padding(bottom = AlfredTheme.spacing.xxl),
        verticalArrangement = Arrangement.spacedBy(AlfredTheme.spacing.s),
    ) {
        Text(view.title.uppercase(), style = type.title, color = colors.textPrimary)
        view.detail?.let { Text(it, style = type.bodySmall, color = colors.textSecondary) }
        Text("Voice: ${voice.link.describe()}", style = type.bodySmall, color = colors.textSecondary)
        OutlinedButton(onClick = onCheckNow) { Text("Check now") }

        val last = voice.voice
        if (last.transcript.isNotEmpty() || last.reply.isNotEmpty()) {
            HorizontalDivider(Modifier.padding(vertical = AlfredTheme.spacing.s))
            Text("LAST TURN", style = type.label, color = colors.textSecondary)
            if (last.transcript.isNotEmpty()) {
                Text("You: ${last.transcript}", style = type.bodySmall, color = colors.textPrimary)
            }
            if (last.reply.isNotEmpty()) {
                Text("Alfred: ${last.reply}", style = type.bodySmall, color = colors.textPrimary)
            }
            last.metadata?.describe()?.forEach {
                Text(it, style = type.bodySmall, color = colors.textSecondary)
            }
        }
        last.error?.let { Text(it, style = type.bodySmall, color = colors.record) }
    }
}

@Composable
private fun ConfirmationSheet(
    confirmation: PendingConfirmation,
    error: String?,
    onAnswer: (Boolean) -> Unit,
) {
    val colors = AlfredTheme.colors
    val type = AlfredTheme.typography
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 24.dp)
            .padding(bottom = 32.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            if (confirmation.irreversible) "Approve this? It can't be undone." else "Go ahead?",
            style = type.title,
            color = colors.textPrimary,
        )
        Text(confirmation.summary, style = type.bodySmall, color = colors.textPrimary)
        if (!confirmation.irreversible) {
            Text("You can also just say yes or no.", style = type.bodySmall, color = colors.textSecondary)
        }
        error?.let { Text(it, color = colors.record) }
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

private data class StatusView(val title: String, val detail: String?)

private fun ConnectionStatus.toView(): StatusView = when (this) {
    ConnectionStatus.Checking -> StatusView("Checking…", null)
    ConnectionStatus.Connected -> StatusView("Connected", null)
    ConnectionStatus.Unauthorised -> StatusView("Unauthorised", "The server rejected the API token.")
    ConnectionStatus.InvalidToken ->
        StatusView("Invalid token", "The API token has characters that can't be sent. Re-enter it in Settings.")
    ConnectionStatus.NotConfigured -> StatusView("Not set up", "Enter the server URL and API token in Settings.")
    is ConnectionStatus.Unreachable ->
        StatusView("Unreachable", "Is Tailscale on and the server running? ($reason)")
    is ConnectionStatus.ServerError -> StatusView("Server error", "The server answered $code.")
}

private val HEADER_BUTTON = 44.dp

// Not a local: lint's ConfigurationScreenWidthHeight check crashes resolving it there.
private val ORB_SIDE = OrbSize.Large.diameter
/** The design's tile width; a longer persona name widens its tile. */
private val TILE_WIDTH = 104.dp
private val MIC_BUTTON = 96.dp

/** The greeting line is a touch bigger than other labels in the design. */
private const val GREETING_SCALE = 1.2f

@Preview(widthDp = 393, heightDp = 851)
@Composable
private fun HomeContentPreview() {
    AlfredTheme {
        GridBackground {
            HomeContent(
                status = ConnectionStatus.Connected,
                voice = VoiceUiState(link = LinkState.Connected("s1", voice = true)),
                level = { 0f },
                profile = ProfileSettings(),
                time = LocalTime.of(2, 14),
                micDenied = false,
                onOpenMenu = {},
                onOpenProtocol = {},
                onCheckNow = {},
                onTalkPressed = { true },
                onTalkReleased = {},
                onMicUnavailable = {},
                onOpenAppSettings = {},
                onAnswer = {},
                onDismissConfirmation = {},
            )
        }
    }
}
