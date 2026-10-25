package com.alfred.app.ui.recording

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.alfred.app.data.voice.MicMeter
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.data.voice.VoicePhase
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/** What the Recording screen shows, apart from the per-frame [MicMeter] and waveform. */
data class RecordingUiState(
    /** A turn is being listened to: the Recording screen is up. */
    val listening: Boolean = false,
    /** When the mic opened (epoch millis), for the REC timer. */
    val sinceMillis: Long? = null,
    val stable: String = "",
    val unstable: String = "",
)

/**
 * The Recording screen (ADR 0021): the live transcript from [VoiceController.state], plus the
 * mic meter and a scrolling history of its levels for the waveform.
 */
class RecordingViewModel(private val voice: VoiceController) : ViewModel() {

    val state: StateFlow<RecordingUiState> = voice.state
        .map {
            RecordingUiState(
                listening = it.phase == VoicePhase.Listening,
                sinceMillis = it.listeningSinceMillis,
                stable = it.partialStable,
                unstable = it.partialUnstable,
            )
        }
        .distinctUntilChanged()
        .stateIn(viewModelScope, SharingStarted.Eagerly, RecordingUiState())

    val meter: StateFlow<MicMeter> = voice.meter

    private val history = LevelHistory()
    private val _bars = MutableStateFlow(history.bars())

    /** Waveform bar heights (0–1), oldest first, newest last. */
    val bars: StateFlow<List<Float>> = _bars.asStateFlow()

    init {
        viewModelScope.launch {
            var seen = 0L
            voice.meter.collect { meter ->
                if (meter.frames < seen || meter.frames == 0L) history.clear()
                // The flow drops readings when the UI is busy; missed frames repeat the latest.
                repeat((meter.frames - seen).coerceIn(0, MAX_CATCH_UP).toInt()) { history.add(meter.level) }
                seen = meter.frames
                _bars.value = history.bars()
            }
        }
    }

    /** The stop button: end of speech, send it. */
    fun stop() = voice.stopListening()

    /** The close button or back: drop the turn; nothing reaches the router. */
    fun cancel() = voice.cancel()

    private companion object {
        const val MAX_CATCH_UP = 50L
    }
}

/**
 * A fixed number of waveform bars, newest last. Each bar is the loudest of [framesPerBar] mic
 * frames, so the waveform scrolls at a steady pace whatever the frame rate.
 */
class LevelHistory(val size: Int = 48, private val framesPerBar: Int = 2) {
    private val ring = FloatArray(size)
    private var next = 0
    private var pending = 0f
    private var pendingFrames = 0

    fun add(level: Float) {
        pending = maxOf(pending, level)
        if (++pendingFrames < framesPerBar) return
        ring[next] = pending
        next = (next + 1) % size
        pending = 0f
        pendingFrames = 0
    }

    fun clear() {
        ring.fill(0f)
        next = 0
        pending = 0f
        pendingFrames = 0
    }

    /** Oldest first; all [size] bars, zero where nothing's been heard yet. */
    fun bars(): List<Float> = List(size) { ring[(next + it) % size] }
}
