package com.alfred.app.data.wakeword

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

@OptIn(ExperimentalCoroutinesApi::class)
class FileWakeEventLogTest {

    @get:Rule
    val folder = TemporaryFolder()

    @Test
    fun `appends JSON lines and counts them`() = runTest(UnconfinedTestDispatcher()) {
        val file = folder.root.resolve("wake_events.jsonl")
        val log = FileWakeEventLog(file, UnconfinedTestDispatcher(testScheduler))
        log.record(WakeEvent(WakeEvent.Kind.Detection, 1_000, StockWakeWord.HeyJarvis, 0.9f))
        log.record(WakeEvent(WakeEvent.Kind.FalseAccept, 2_000, StockWakeWord.HeyJarvis, 0.9f))
        log.record(WakeEvent(WakeEvent.Kind.Detection, 3_000, StockWakeWord.Alexa, 0.6f))

        assertEquals(WakeCounts(detections = 2, falseAccepts = 1), log.counts.value)
        val lines = file.readLines().map(::JSONObject)
        assertEquals(listOf("detection", "false_accept", "detection"), lines.map { it.getString("kind") })
        assertEquals(3_000L, lines[2].getLong("at"))
        assertEquals("Alexa", lines[2].getString("model"))
        assertEquals(0.6, lines[2].getDouble("score"), 1e-6)
    }

    @Test
    fun `counts what an earlier run wrote`() = runTest(UnconfinedTestDispatcher()) {
        val file = folder.root.resolve("wake_events.jsonl")
        file.writeText(
            """
            {"kind":"detection","at":1,"model":"HeyJarvis","score":0.9}
            not json
            {"kind":"false_accept","at":2,"model":"HeyJarvis","score":0.9}
            {"kind":"detection","at":3,"model":"HeyJarvis","score":0.7}
            """.trimIndent() + "\n",
        )
        val log = FileWakeEventLog(file, UnconfinedTestDispatcher(testScheduler))
        log.load()
        assertEquals(WakeCounts(detections = 2, falseAccepts = 1), log.counts.value)
        log.record(WakeEvent(WakeEvent.Kind.Detection, 4, StockWakeWord.HeyJarvis, 0.8f))
        assertEquals(WakeCounts(detections = 3, falseAccepts = 1), log.counts.value)
    }
}
