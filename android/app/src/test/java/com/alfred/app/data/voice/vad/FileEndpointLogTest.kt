package com.alfred.app.data.voice.vad

import com.alfred.app.data.voice.Endpoint
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

@OptIn(ExperimentalCoroutinesApi::class)
class FileEndpointLogTest {

    @get:Rule
    val folder = TemporaryFolder()

    private fun record(at: Long, endpoint: Endpoint = Endpoint.End) = EndpointRecord(
        atMillis = at,
        mode = TurnMode.HandsFree,
        report = if (endpoint == Endpoint.End) {
            EndpointReport(endpoint, "silence", 3_200, 640, 2_464, 736, 280)
        } else {
            EndpointReport(endpoint, "no_speech", 5_024, null, null, null, 250)
        },
        config = VadConfig(),
    )

    @Test
    fun `appends JSON lines and keeps the latest first`() = runTest(UnconfinedTestDispatcher()) {
        val file = folder.root.resolve("endpoint_events.jsonl")
        val log = FileEndpointLog(file, UnconfinedTestDispatcher(testScheduler))
        log.record(record(1_000))
        log.record(record(2_000, Endpoint.NoSpeech))

        assertEquals(listOf(2_000L, 1_000L), log.recent.value.map { it.atMillis })
        val lines = file.readLines().map(::JSONObject)
        assertEquals(listOf("silence", "no_speech"), lines.map { it.getString("reason") })
        assertEquals(736, lines[0].getInt("latency_ms"))
        assertEquals("hands_free", lines[0].getString("mode"))
        assertEquals("silero", lines[0].getString("engine"))
        assertTrue(lines[1].isNull("latency_ms"))
        assertEquals(700, lines[0].getJSONObject("config").getInt("end_silence_ms"))
    }

    @Test
    fun `loads what's stored, skipping lines it can't read`() = runTest(UnconfinedTestDispatcher()) {
        val file = folder.root.resolve("endpoint_events.jsonl")
        val dispatcher = UnconfinedTestDispatcher(testScheduler)
        val first = FileEndpointLog(file, dispatcher)
        repeat(12) { first.record(record(it.toLong())) }
        file.appendText("not json\n")

        val reopened = FileEndpointLog(file, dispatcher)
        reopened.load()

        assertEquals(FileEndpointLog.RECENT - 1, reopened.recent.value.size)
        assertEquals(11L, reopened.recent.value.first().atMillis)
        assertEquals(record(11), reopened.recent.value.first())
    }
}
