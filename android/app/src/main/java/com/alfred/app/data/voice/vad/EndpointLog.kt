package com.alfred.app.data.voice.vad

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

/** A hands-free turn (wake word) or a push-to-talk one. */
enum class TurnMode(val wire: String) {
    HandsFree("hands_free"),
    PushToTalk("push_to_talk"),
}

/** One turn's end, as logged. */
data class EndpointRecord(val atMillis: Long, val mode: TurnMode, val report: EndpointReport, val config: VadConfig)

/** How each turn's end was decided, kept for tuning (ADR 0023). */
interface EndpointLog {
    /** The latest records, newest first, for the developer screen. */
    val recent: StateFlow<List<EndpointRecord>>

    /** Fills [recent] from what's stored; [record] does this itself the first time. */
    suspend fun load()

    suspend fun record(record: EndpointRecord)
}

/**
 * JSON lines in the app's files directory (`endpoint_events.jsonl`), one object per turn. Pull
 * it with `adb shell run-as com.alfred.app cat files/endpoint_events.jsonl`.
 */
class FileEndpointLog(
    private val file: File,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : EndpointLog {
    private val mutex = Mutex()
    private var loaded = false
    private val _recent = MutableStateFlow<List<EndpointRecord>>(emptyList())
    override val recent: StateFlow<List<EndpointRecord>> = _recent.asStateFlow()

    override suspend fun load() = mutex.withLock { loadLocked() }

    override suspend fun record(record: EndpointRecord) = mutex.withLock {
        loadLocked()
        val line = encode(record).toString()
        withContext(ioDispatcher) {
            file.parentFile?.mkdirs()
            file.appendText(line + "\n")
        }
        _recent.value = (listOf(record) + _recent.value).take(RECENT)
    }

    private suspend fun loadLocked() {
        if (loaded) return
        loaded = true
        _recent.value = withContext(ioDispatcher) {
            if (!file.exists()) return@withContext emptyList()
            file.readLines().takeLast(RECENT).mapNotNull(::decode).reversed()
        }
    }

    companion object {
        const val RECENT = 10

        fun encode(record: EndpointRecord): JSONObject {
            val report = record.report
            val config = record.config
            return JSONObject()
                .put("at", record.atMillis)
                .put("mode", record.mode.wire)
                .put("endpoint", report.endpoint.name)
                .put("reason", report.reason)
                .put("total_ms", report.totalMillis)
                .put("speech_start_ms", report.speechStartMillis ?: JSONObject.NULL)
                .put("last_speech_ms", report.lastSpeechMillis ?: JSONObject.NULL)
                .put("latency_ms", report.latencyMillis ?: JSONObject.NULL)
                .put("inference_us", report.meanInferenceMicros)
                .put(
                    "config",
                    JSONObject()
                        .put("threshold", config.threshold.toDouble())
                        .put("end_silence_ms", config.endSilenceMillis)
                        .put("no_speech_ms", config.noSpeechMillis)
                        .put("max_utterance_ms", config.maxUtteranceMillis)
                        .put("min_speech_ms", config.minSpeechMillis),
                )
        }

        private fun decode(line: String): EndpointRecord? = try {
            val json = JSONObject(line)
            val config = json.getJSONObject("config")
            EndpointRecord(
                atMillis = json.getLong("at"),
                mode = TurnMode.entries.first { it.wire == json.getString("mode") },
                report = EndpointReport(
                    endpoint = enumValueOf(json.getString("endpoint")),
                    reason = json.getString("reason"),
                    totalMillis = json.getInt("total_ms"),
                    speechStartMillis = json.optIntOrNull("speech_start_ms"),
                    lastSpeechMillis = json.optIntOrNull("last_speech_ms"),
                    latencyMillis = json.optIntOrNull("latency_ms"),
                    meanInferenceMicros = json.optLong("inference_us"),
                ),
                config = VadConfig(
                    threshold = config.getDouble("threshold").toFloat(),
                    endSilenceMillis = config.getInt("end_silence_ms"),
                    noSpeechMillis = config.getInt("no_speech_ms"),
                    maxUtteranceMillis = config.getInt("max_utterance_ms"),
                    minSpeechMillis = config.getInt("min_speech_ms"),
                ),
            )
        } catch (e: JSONException) {
            null
        } catch (e: IllegalArgumentException) {
            null
        } catch (e: NoSuchElementException) {
            null
        }

        private fun JSONObject.optIntOrNull(key: String): Int? = if (isNull(key)) null else getInt(key)
    }
}
