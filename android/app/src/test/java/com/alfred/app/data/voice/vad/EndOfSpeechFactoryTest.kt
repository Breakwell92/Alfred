package com.alfred.app.data.voice.vad

import com.alfred.app.data.voice.Endpoint
import com.alfred.app.data.voice.EnergyEndOfSpeech
import com.alfred.app.testing.FakeEndpointLog
import com.alfred.app.testing.FakeVadSettingsRepository
import com.alfred.app.testing.LoudnessVadModel
import com.alfred.app.testing.latest
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class EndOfSpeechFactoryTest {

    private val settings = FakeVadSettingsRepository()
    private val log = FakeEndpointLog()
    private val logcat = mutableListOf<String>()

    private fun TestScope.factory(load: () -> VadModel = ::LoudnessVadModel) = EndOfSpeechFactory(
        settingsRepository = settings,
        log = log,
        loadModel = load,
        scope = backgroundScope,
        loadDispatcher = UnconfinedTestDispatcher(testScheduler),
        clock = { 42L },
        logcat = { logcat += it },
    )

    private fun frame(level: Int): ByteArray = ByteArray(640).also { out ->
        for (i in 0 until 320) {
            out[2 * i] = level.toByte()
            out[2 * i + 1] = (level shr 8).toByte()
        }
    }

    private fun runFactoryTest(block: suspend TestScope.() -> Unit) =
        runTest(UnconfinedTestDispatcher(), testBody = block)

    @Test
    fun `silero once the model has loaded, and each turn is logged`() = runFactoryTest {
        val factory = factory()
        assertEquals(VadEngineStatus.Ready, factory.status.value)
        val eos = factory.handsFree()
        assertTrue(eos is SileroEndOfSpeech)

        val results = (List(40) { frame(8_000) } + List(40) { frame(10) }).map(eos::onFrame)

        assertEquals(Endpoint.End, results.last())
        val record = checkNotNull(log.recent.latest)
        assertEquals(TurnMode.HandsFree, record.mode)
        assertEquals(42L, record.atMillis)
        assertEquals("silence", record.report.reason)
        assertTrue(logcat.single().startsWith("hands_free turn: silence"))
    }

    @Test
    fun `push-to-talk never gives up before anyone speaks`() = runFactoryTest {
        val eos = factory().pushToTalk()
        val results = List(1_000) { frame(10) }.map(eos::onFrame)
        assertTrue(results.all { it == Endpoint.Continue })
    }

    @Test
    fun `the developer settings reach new turns`() = runFactoryTest {
        val factory = factory()
        settings.update { it.copy(config = it.config.copy(noSpeechMillis = 2_000)) }
        val eos = factory.handsFree()
        val results = List(110) { frame(10) }.map(eos::onFrame)
        assertEquals(Endpoint.NoSpeech, results.last())
        assertEquals(2_000, log.recent.latest?.config?.noSpeechMillis)
    }

    @Test
    fun `energy when silero is turned off`() = runFactoryTest {
        settings.update { it.copy(useSilero = false) }
        assertTrue(factory().handsFree() is EnergyEndOfSpeech)
    }

    @Test
    fun `energy when the model can't load`() = runFactoryTest {
        val factory = factory { throw IllegalStateException("no model") }
        assertEquals(VadEngineStatus.Failed("no model"), factory.status.value)
        assertTrue(factory.handsFree() is EnergyEndOfSpeech)
        assertTrue(logcat.single().contains("no model"))
    }

    @Test
    fun `energy when onnx runtime isn't built for the device`() = runFactoryTest {
        val factory = factory { throw UnsatisfiedLinkError("libonnxruntime.so") }
        assertTrue(factory.status.value is VadEngineStatus.Failed)
        assertTrue(factory.pushToTalk() is EnergyEndOfSpeech)
    }
}
