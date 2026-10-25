package com.alfred.app.testing

import com.alfred.app.data.wakeword.EMBEDDING_SIZE
import com.alfred.app.data.wakeword.MEL_BINS
import com.alfred.app.data.wakeword.WakeCounts
import com.alfred.app.data.wakeword.WakeEvent
import com.alfred.app.data.wakeword.WakeEventLog
import com.alfred.app.data.wakeword.WakeFeedback
import com.alfred.app.data.wakeword.WakeSignals
import com.alfred.app.data.wakeword.WakeWordModels
import com.alfred.app.data.wakeword.WakeWordSettings
import com.alfred.app.data.wakeword.WakeWordSettingsRepository
import kotlinx.coroutines.flow.MutableStateFlow

/**
 * Stand-in for the ONNX models. The mel "model" gives one frame of [melValue] per 160 samples
 * (like the real one, roughly); [score] returns [scores] in order, then [defaultScore].
 */
class FakeWakeWordModels(var defaultScore: Float = 0f, var melValue: Float = 7f) : WakeWordModels {
    val scores = ArrayDeque<Float>()
    val melInputs = mutableListOf<FloatArray>()
    val embedInputs = mutableListOf<Array<FloatArray>>()
    val scoreInputs = mutableListOf<Array<FloatArray>>()

    override fun melSpectrogram(samples: FloatArray): Array<FloatArray> {
        melInputs += samples
        return Array(maxOf(1, samples.size / 160)) { FloatArray(MEL_BINS) { melValue } }
    }

    override fun embed(window: Array<FloatArray>): FloatArray {
        embedInputs += window
        return FloatArray(EMBEDDING_SIZE) { embedInputs.size.toFloat() }
    }

    /** Calls in order ("score-start", "score-end", "close"), from whichever thread made them. */
    val calls: MutableList<String> = java.util.Collections.synchronizedList(mutableListOf())

    /** If set, [score] waits on it: lets a test hold inference "in progress". */
    @Volatile
    var holdScore: java.util.concurrent.CountDownLatch? = null

    override fun score(features: Array<FloatArray>): Float {
        calls += "score-start"
        holdScore?.await()
        scoreInputs += features
        calls += "score-end"
        return scores.removeFirstOrNull() ?: defaultScore
    }

    @Volatile
    var closed = false
        private set

    override fun close() {
        calls += "close"
        closed = true
    }
}

/** [chunks] × 80 ms of PCM16 audio at a constant [value]. */
fun pcm(chunks: Int, value: Short = 0): ByteArray {
    val samples = chunks * 1280
    return ByteArray(samples * 2).also { out ->
        for (i in 0 until samples) {
            out[2 * i] = value.toByte()
            out[2 * i + 1] = (value.toInt() shr 8).toByte()
        }
    }
}

class FakeWakeWordSettingsRepository(initial: WakeWordSettings = WakeWordSettings(enabled = true)) :
    WakeWordSettingsRepository {
    override val settings = MutableStateFlow(initial)

    override suspend fun update(transform: (WakeWordSettings) -> WakeWordSettings) {
        settings.value = transform(settings.value)
    }
}

class FakeWakeSignals : WakeSignals {
    override val otherAppRecording = MutableStateFlow(false)
    override val inCall = MutableStateFlow(false)
    override val doNotDisturb = MutableStateFlow(false)
}

/** Records each wake and whether the mic was still open at that moment. */
class FakeWakeFeedback(private val micOpen: () -> Boolean = { false }) : WakeFeedback {
    val wakes = mutableListOf<Boolean>()
    val micOpenAtWake = mutableListOf<Boolean>()

    override suspend fun onWake(chime: Boolean) {
        wakes += chime
        micOpenAtWake += micOpen()
    }
}

class FakeWakeEventLog : WakeEventLog {
    val events = mutableListOf<WakeEvent>()
    override val counts = MutableStateFlow(WakeCounts())
    var loads = 0

    override suspend fun load() {
        loads++
    }

    override suspend fun record(event: WakeEvent) {
        events += event
        counts.value = WakeCounts(
            detections = events.count { it.kind == WakeEvent.Kind.Detection },
            falseAccepts = events.count { it.kind == WakeEvent.Kind.FalseAccept },
        )
    }
}
