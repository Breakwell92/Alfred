package com.alfred.app.data.wakeword

import java.io.File
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONException
import org.json.JSONObject

/** One wake, or a "That wasn't me" report about one. */
data class WakeEvent(val kind: Kind, val atMillis: Long, val model: StockWakeWord, val score: Float) {
    enum class Kind(val wire: String) {
        Detection("detection"),
        FalseAccept("false_accept"),
    }
}

data class WakeCounts(val detections: Int = 0, val falseAccepts: Int = 0)

/** Wakes and false-accept reports, kept for tuning the threshold (ADR 0019). */
interface WakeEventLog {
    val counts: StateFlow<WakeCounts>

    /** Fills [counts] from what's stored; [record] does this itself the first time. */
    suspend fun load()

    suspend fun record(event: WakeEvent)
}

/**
 * JSON lines in the app's files directory (`wake_events.jsonl`), one object per event:
 * `{"kind", "at" (epoch ms), "model", "score"}`. Pull it with
 * `adb shell run-as com.alfred.app cat files/wake_events.jsonl`.
 */
class FileWakeEventLog(
    private val file: File,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : WakeEventLog {
    private val mutex = Mutex()
    private var loaded = false
    private val _counts = MutableStateFlow(WakeCounts())
    override val counts: StateFlow<WakeCounts> = _counts.asStateFlow()

    override suspend fun load() = mutex.withLock { loadLocked() }

    override suspend fun record(event: WakeEvent) = mutex.withLock {
        loadLocked()
        val line = JSONObject()
            .put("kind", event.kind.wire)
            .put("at", event.atMillis)
            .put("model", event.model.name)
            .put("score", event.score.toDouble())
            .toString()
        withContext(ioDispatcher) {
            file.parentFile?.mkdirs()
            file.appendText(line + "\n")
        }
        _counts.value = _counts.value.add(event.kind)
    }

    private suspend fun loadLocked() {
        if (loaded) return
        loaded = true
        _counts.value = withContext(ioDispatcher) {
            if (!file.exists()) return@withContext WakeCounts()
            file.readLines().fold(WakeCounts()) { counts, line ->
                val kind = try {
                    JSONObject(line).optString("kind")
                } catch (e: JSONException) {
                    ""
                }
                WakeEvent.Kind.entries.firstOrNull { it.wire == kind }?.let { counts.add(it) } ?: counts
            }
        }
    }

    private fun WakeCounts.add(kind: WakeEvent.Kind): WakeCounts = when (kind) {
        WakeEvent.Kind.Detection -> copy(detections = detections + 1)
        WakeEvent.Kind.FalseAccept -> copy(falseAccepts = falseAccepts + 1)
    }
}
