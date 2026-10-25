package com.alfred.app.data.reliability

import java.io.File
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** Something that happened to the wake word service or the socket (ADR 0025). */
data class ReliabilityEvent(
    val kind: Kind,
    val atMillis: Long,
    /** A [StartReason] wire name, or who noticed (`watchdog`, `boot`). */
    val reason: String? = null,
    /** For `started` after a death and for `down`: how long it was (or has been) without the service. */
    val gapMillis: Long? = null,
) {
    enum class Kind(val wire: String) {
        Started("started"),
        Stopped("stopped"),
        Down("down"),
        RestartBlocked("restart_blocked"),
        Reconnect("reconnect"),
    }
}

interface ReliabilityLog {
    suspend fun record(event: ReliabilityEvent)
}

/**
 * JSON lines in the app's files directory (`reliability_events.jsonl`), one object per event:
 * `{"kind", "at" (epoch ms), "reason"?, "gap_ms"?}`. Pull it with
 * `adb shell run-as com.alfred.app cat files/reliability_events.jsonl`.
 */
class FileReliabilityLog(
    private val file: File,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : ReliabilityLog {
    private val mutex = Mutex()

    override suspend fun record(event: ReliabilityEvent) = mutex.withLock {
        val line = JSONObject()
            .put("kind", event.kind.wire)
            .put("at", event.atMillis)
            .putOpt("reason", event.reason)
            .putOpt("gap_ms", event.gapMillis)
            .toString()
        withContext(ioDispatcher) {
            file.parentFile?.mkdirs()
            file.appendText(line + "\n")
        }
    }
}
