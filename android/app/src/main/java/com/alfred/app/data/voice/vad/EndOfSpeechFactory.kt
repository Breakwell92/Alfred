package com.alfred.app.data.voice.vad

import android.util.Log
import com.alfred.app.data.voice.EndOfSpeechDetector
import com.alfred.app.data.voice.EnergyEndOfSpeech
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/** Whether Silero VAD is ready, for the developer screen. */
sealed interface VadEngineStatus {
    data object Loading : VadEngineStatus

    data object Ready : VadEngineStatus

    /** It couldn't load: turns use the energy endpointer. */
    data class Failed(val message: String) : VadEngineStatus
}

/**
 * Builds each turn's [EndOfSpeechDetector] (ADR 0024). The Silero model is loaded once, off the
 * main thread, as soon as the app starts; until it's ready, if it can't load, or with Silero
 * turned off on the developer screen, turns use [EnergyEndOfSpeech]. Every turn that ends itself
 * is logged to [log] (and logcat), with the engine that decided. Call it from the main thread.
 */
class EndOfSpeechFactory(
    settingsRepository: VadSettingsRepository,
    private val log: EndpointLog,
    loadModel: () -> VadModel,
    private val scope: CoroutineScope,
    loadDispatcher: CoroutineDispatcher = Dispatchers.Default,
    private val clock: () -> Long = System::currentTimeMillis,
    private val logcat: (String) -> Unit = { Log.i(TAG, it) },
) {
    val settings: StateFlow<VadSettings> =
        settingsRepository.settings.stateIn(scope, SharingStarted.Eagerly, VadSettings())

    private val _status = MutableStateFlow<VadEngineStatus>(VadEngineStatus.Loading)
    val status: StateFlow<VadEngineStatus> = _status.asStateFlow()

    @Volatile
    private var model: VadModel? = null

    init {
        scope.launch(loadDispatcher) {
            try {
                model = loadModel()
                _status.value = VadEngineStatus.Ready
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                failed(e.message ?: e.javaClass.simpleName)
            } catch (e: LinkageError) {
                // ONNX Runtime's native library isn't built for this device's ABI.
                failed("ONNX Runtime doesn't run on this device")
            }
        }
    }

    /** For a wake word turn: ends on silence, gives up when nobody speaks, caps the length. */
    fun handsFree(): EndOfSpeechDetector = detector(TurnMode.HandsFree, settings.value.config)

    /**
     * For push-to-talk's "or go quiet" (and CUT IN): silence after speech sends the turn. Nothing
     * ends it before anyone speaks (the button is waiting), and
     * [com.alfred.app.data.voice.VoiceController] caps the length. While [held] is true (the
     * hold-to-talk button is still down) it never ends: the release decides, and nothing is logged
     * for an end that didn't happen.
     */
    fun pushToTalk(held: () -> Boolean = { false }): EndOfSpeechDetector = detector(
        TurnMode.PushToTalk,
        settings.value.config.copy(noSpeechMillis = Int.MAX_VALUE, maxUtteranceMillis = Int.MAX_VALUE),
        held,
    )

    private fun detector(mode: TurnMode, config: VadConfig, held: () -> Boolean = { false }): EndOfSpeechDetector {
        val onReport = { report: EndpointReport ->
            logcat(
                "${mode.wire} turn (${report.engine.wire}): ${report.reason} after ${report.totalMillis} ms, " +
                    "${report.latencyMillis ?: "-"} ms after the last speech, ${report.meanInferenceMicros} µs/chunk",
            )
            val record = EndpointRecord(clock(), mode, report, config)
            scope.launch { log.record(record) }
            Unit
        }
        val model = model
        if (model == null || !settings.value.useSilero) {
            return EnergyEndOfSpeech(
                noSpeechMillis = config.noSpeechMillis,
                maxMillis = config.maxUtteranceMillis,
                held = held,
                onReport = onReport,
            )
        }
        return SileroEndOfSpeech(VadStream(model), config, onReport = onReport, held = held)
    }

    private fun failed(message: String) {
        _status.value = VadEngineStatus.Failed(message)
        logcat("Silero VAD unavailable, using the energy endpointer: $message")
    }

    private companion object {
        const val TAG = "Alfred/VAD"
    }
}
