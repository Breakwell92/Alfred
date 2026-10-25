package com.alfred.app.data.reliability

import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.runTest
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class FileReliabilityLogTest {

    @get:Rule
    val folder = TemporaryFolder()

    @Test
    fun `appends one JSON line per event, leaving out what isn't set`() = runTest {
        val file = File(folder.root, "files/reliability_events.jsonl")
        val log = FileReliabilityLog(file, Dispatchers.Unconfined)

        log.record(ReliabilityEvent(ReliabilityEvent.Kind.Started, 1_000, "boot", 720_000))
        log.record(ReliabilityEvent(ReliabilityEvent.Kind.Reconnect, 2_000))

        val lines = file.readLines().map(::JSONObject)
        assertEquals(2, lines.size)
        assertEquals("started", lines[0].getString("kind"))
        assertEquals(1_000L, lines[0].getLong("at"))
        assertEquals("boot", lines[0].getString("reason"))
        assertEquals(720_000L, lines[0].getLong("gap_ms"))
        assertEquals("reconnect", lines[1].getString("kind"))
        assertFalse(lines[1].has("reason"))
        assertFalse(lines[1].has("gap_ms"))
    }
}
