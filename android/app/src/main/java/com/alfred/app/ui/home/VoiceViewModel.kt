package com.alfred.app.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.alfred.app.data.voice.DeviceConnection
import com.alfred.app.data.voice.EndOfSpeechDetector
import com.alfred.app.data.voice.Endpoint
import com.alfred.app.data.voice.LinkState
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.data.voice.VoicePhase
import com.alfred.app.data.voice.VoiceState
import com.alfred.app.data.voice.goQuiet
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn

data class VoiceUiState(val voice: VoiceState = VoiceState(), val link: LinkState = LinkState.Offline)

/**
 * Push-to-talk on Home. The talk button works both ways: hold it and let go to send (hold to
 * talk), or tap once to start and tap again to send (toggle). Tapping while Alfred is thinking
 * or speaking stops him. A turn also sends itself once the speaker goes quiet ([endOfSpeech],
 * the Recording screen's "or go quiet", ADR 0021 and 0024), but never while the button is still held:
 * then letting go decides, so a pause to think doesn't cut the request in half.
 */
class VoiceViewModel(
    private val voice: VoiceController,
    private val connection: DeviceConnection,
    /** End of speech with no no-speech or length limit ([com.alfred.app.data.voice.vad.EndOfSpeechFactory.pushToTalk]). */
    private val endOfSpeech: () -> EndOfSpeechDetector = ::goQuiet,
) : ViewModel() {

    val state: StateFlow<VoiceUiState> = combine(voice.state, connection.state, ::VoiceUiState)
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(STOP_TIMEOUT_MILLIS), VoiceUiState())

    /** The orb's level (mic while listening, reply while speaking); separate as it changes every 20 ms. */
    val level: StateFlow<Float> = voice.level

    /** Whether the press now down started the turn (so a long hold's release sends it). */
    private var pressStartedTurn = false

    /** The button that started the turn is still down: silence mustn't end it. */
    private var held = false

    fun onTalkPressed() {
        pressStartedTurn = false
        when (voice.state.value.phase) {
            VoicePhase.Idle -> {
                val detector = endOfSpeech()
                held = true
                // The inner detector still sees every frame, so its noise floor stays current.
                pressStartedTurn = voice.startListening({ frame ->
                    val endpoint = detector.onFrame(frame)
                    if (held) Endpoint.Continue else endpoint
                })
                if (!pressStartedTurn) held = false
            }
            VoicePhase.Listening -> voice.stopListening()
            VoicePhase.Thinking, VoicePhase.Speaking -> voice.cancel()
        }
    }

    /** [heldMillis]: how long the button was down. A short tap leaves the mic open (toggle). */
    fun onTalkReleased(heldMillis: Long) {
        if (pressStartedTurn && heldMillis >= HOLD_THRESHOLD_MILLIS &&
            voice.state.value.phase == VoicePhase.Listening
        ) {
            voice.stopListening()
        }
        pressStartedTurn = false
        held = false
    }

    /** The mic was tapped while the link is down: try again now rather than at the next backoff. */
    fun reconnect() = connection.reconnectNow()

    fun answer(approve: Boolean) = voice.answer(approve)

    fun dismissConfirmation() = voice.dismissConfirmation()

    companion object {
        /** Held at least this long counts as hold-to-talk; shorter is a tap. */
        const val HOLD_THRESHOLD_MILLIS = 400L
        private const val STOP_TIMEOUT_MILLIS = 5_000L
    }
}
