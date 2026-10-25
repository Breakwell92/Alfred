package com.alfred.app.ui.transmitting

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.alfred.app.data.voice.EndOfSpeechDetector
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.data.voice.VoicePhase
import com.alfred.app.data.voice.goQuiet
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/** What the Transmitting screen shows, apart from the heard position and the orb's level. */
data class TransmittingUiState(
    /** The layer is up: Alfred is thinking or speaking, or just finished (the hold). */
    val visible: Boolean = false,
    val phase: VoicePhase = VoicePhase.Idle,
    val reply: String = "",
    /** The reply's timed words, found in [reply]. */
    val spans: List<WordSpan> = emptyList(),
    val highlight: Highlight = Highlight.Plain,
    /** What went wrong with the turn, shown during the hold. */
    val error: String? = null,
)

/**
 * The Transmitting screen (ADR 0023): Alfred's reply, highlighted word by word as it's heard.
 * It comes up when a turn moves on from listening, and stays [holdMillis] after the turn ends so
 * the last words can be read, then gives way to Home. Result cards (STU-117) will keep it up.
 */
class TransmittingViewModel(
    private val voice: VoiceController,
    private val endOfSpeech: () -> EndOfSpeechDetector = ::goQuiet,
    private val holdMillis: Long = HOLD_MILLIS,
) : ViewModel() {

    private val visible = MutableStateFlow(false)
    private var holdJob: Job? = null

    val state: StateFlow<TransmittingUiState> = combine(voice.state, visible) { voice, shown ->
        TransmittingUiState(
            visible = shown,
            phase = voice.phase,
            reply = voice.reply,
            spans = alignWords(voice.reply, voice.words),
            highlight = highlightFor(
                speaking = voice.phase == VoicePhase.Speaking,
                thinking = voice.phase == VoicePhase.Thinking,
                hasWords = voice.words.isNotEmpty(),
            ),
            error = voice.error,
        )
    }
        .distinctUntilChanged()
        .stateIn(viewModelScope, SharingStarted.Eagerly, TransmittingUiState())

    private val _heard = MutableStateFlow(0L)

    /**
     * How far into the reply the listener is (ms). Never goes back within a turn: the player's
     * position drops to 0 as playback ends, before the turn does.
     */
    val heard: StateFlow<Long> = _heard.asStateFlow()

    /** The orb's level: the reply's loudness as it's heard. */
    val level: StateFlow<Float> = voice.level

    init {
        viewModelScope.launch {
            voice.state.map { it.phase }.distinctUntilChanged().collect(::onPhase)
        }
        viewModelScope.launch {
            voice.playbackPosition.collect { position -> _heard.value = maxOf(_heard.value, position) }
        }
    }

    private fun onPhase(phase: VoicePhase) {
        when (phase) {
            VoicePhase.Thinking, VoicePhase.Speaking -> {
                if (phase == VoicePhase.Thinking) _heard.value = 0
                holdJob?.cancel()
                visible.value = true
            }
            // A new turn: Recording takes over.
            VoicePhase.Listening -> {
                holdJob?.cancel()
                _heard.value = 0
                visible.value = false
            }
            VoicePhase.Idle -> if (visible.value) {
                holdJob?.cancel()
                holdJob = viewModelScope.launch {
                    delay(holdMillis)
                    visible.value = false
                }
            }
        }
    }

    /** The close button or back: stop Alfred and go straight back to Home. */
    fun close() {
        holdJob?.cancel()
        visible.value = false
        voice.cancel()
    }

    /** CUT IN: silence the reply and start a new turn (Recording takes over). */
    fun cutIn() {
        holdJob?.cancel()
        voice.cutIn(endOfSpeech())
    }

    companion object {
        /** How long the finished reply stays up before Home comes back. */
        const val HOLD_MILLIS = 2_000L
    }
}
