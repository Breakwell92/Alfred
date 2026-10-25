package com.alfred.app.ui.transmitting

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.alfred.app.data.voice.SpokenWord
import com.alfred.app.data.voice.VoicePhase
import com.alfred.app.ui.components.ChamferIconButton
import com.alfred.app.ui.components.GridBackground
import com.alfred.app.ui.components.HexagonButton
import com.alfred.app.ui.components.StatusPill
import com.alfred.app.ui.orb.AlfredOrb
import com.alfred.app.ui.orb.OrbSize
import com.alfred.app.ui.orb.OrbState
import com.alfred.app.ui.theme.AlfredTheme

/**
 * Screen 3 of the design, shown over everything while Alfred thinks and speaks (ADR 0023): the
 * orb, the reply highlighted as it's heard, and CUT IN.
 */
@Composable
fun TransmittingScreen(viewModel: TransmittingViewModel, modifier: Modifier = Modifier) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val heard = viewModel.heard.collectAsStateWithLifecycle()
    val level = viewModel.level.collectAsStateWithLifecycle()
    BackHandler(onBack = viewModel::close)
    TransmittingContent(
        state = state,
        heard = { heard.value },
        level = { level.value },
        onClose = viewModel::close,
        onCutIn = viewModel::cutIn,
        modifier = modifier,
    )
}

/**
 * Stateless Transmitting screen. [heard] and [level] change every 20 ms and are read only where
 * they're used: the orb, and the reply, which recomposes only when the spoken word changes.
 * [resultCards] is the slot for STU-117's cards, under the reply.
 */
@Composable
fun TransmittingContent(
    state: TransmittingUiState,
    heard: () -> Long,
    level: () -> Float,
    onClose: () -> Unit,
    onCutIn: () -> Unit,
    modifier: Modifier = Modifier,
    resultCards: (@Composable () -> Unit)? = null,
) {
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
                    contentDescription = "Stop",
                    onClick = onClose,
                    modifier = Modifier.align(Alignment.CenterStart),
                )
                StatusPill(label = pillLabel(state.phase))
            }

            Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                Orb(state.phase, level)
            }

            Reply(
                state = state,
                heard = heard,
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f),
            )

            resultCards?.invoke()

            Box(
                Modifier
                    .fillMaxWidth()
                    .padding(bottom = spacing.s),
                contentAlignment = Alignment.Center,
            ) {
                HexagonButton(text = "Cut in", onClick = onCutIn, icon = Icons.Filled.Pause)
            }
        }
    }
}

/** Its own composable, so a new level redraws only the orb. */
@Composable
private fun Orb(phase: VoicePhase, level: () -> Float) {
    AlfredOrb(state = orbState(phase), level = level(), size = OrbSize.Small, showGuides = false)
}

@Composable
private fun Reply(state: TransmittingUiState, heard: () -> Long, modifier: Modifier = Modifier) {
    val colors = AlfredTheme.colors
    val type = AlfredTheme.typography
    val spans = state.spans
    // Recomposes when the spoken word changes, not on every position update.
    val current by remember(spans) { derivedStateOf { currentWord(spans, heard()) } }
    val position = if (current >= 0) spans[current].word.startMillis else -1L
    val text = replyText(state.reply, spans, position, state.highlight, colors)
    val scroll = rememberScrollState()
    var layout by remember { mutableStateOf<TextLayoutResult?>(null) }
    // Keep the line being spoken in view, a line of the heard text above it.
    LaunchedEffect(current, layout, state.highlight) {
        val result = layout ?: return@LaunchedEffect
        if (state.highlight != Highlight.FollowAudio || current < 0) return@LaunchedEffect
        val line = result.getLineForOffset(spans[current].start.coerceAtMost(result.layoutInput.text.length))
        val target = result.getLineTop((line - 1).coerceAtLeast(0))
        scroll.animateScrollTo(target.toInt().coerceAtMost(scroll.maxValue))
    }
    Column(modifier.verticalScroll(scroll), verticalArrangement = Arrangement.spacedBy(AlfredTheme.spacing.m)) {
        Text(text = text, style = type.body, onTextLayout = { layout = it })
        val error = state.error
        if (error != null && state.phase == VoicePhase.Idle) {
            Text(error, style = type.bodySmall, color = colors.record)
        }
    }
}

internal fun pillLabel(phase: VoicePhase): String =
    if (phase == VoicePhase.Thinking) "Alfred · thinking" else "Alfred · transmitting"

internal fun orbState(phase: VoicePhase): OrbState = when (phase) {
    VoicePhase.Thinking -> OrbState.Thinking
    VoicePhase.Speaking -> OrbState.Speaking
    VoicePhase.Listening -> OrbState.Listening
    VoicePhase.Idle -> OrbState.Idle
}

/** A reply part-way through being spoken, for previews and the screenshot test. */
internal fun previewState(): TransmittingUiState {
    val reply = "Your meeting with Lucius is at 3:45 pm tomorrow, in the Wayne Tower boardroom. " +
        "Shall I remind you an hour before?"
    var at = 0L
    val words = reply.split(" ").map { word ->
        val length = 120L + word.length * 45L
        SpokenWord(word, at, at + length, emphasis = word.any(Char::isDigit) || word == "pm").also { at += length }
    }
    return TransmittingUiState(
        visible = true,
        phase = VoicePhase.Speaking,
        reply = reply,
        spans = alignWords(reply, words),
        highlight = Highlight.FollowAudio,
    )
}

/** Heard up to "tomorrow," in [previewState]. */
internal fun previewHeard(state: TransmittingUiState): Long =
    state.spans.first { it.word.word == "tomorrow," }.word.startMillis + 10

@Preview(widthDp = 362, heightDp = 784)
@Composable
private fun TransmittingContentPreview() {
    AlfredTheme {
        val state = previewState()
        TransmittingContent(
            state = state,
            heard = { previewHeard(state) },
            level = { 0.5f },
            onClose = {},
            onCutIn = {},
        )
    }
}
