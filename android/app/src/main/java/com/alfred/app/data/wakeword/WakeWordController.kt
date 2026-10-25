package com.alfred.app.data.wakeword

import com.alfred.app.data.voice.AudioCapture
import com.alfred.app.data.voice.EndOfSpeechDetector
import com.alfred.app.data.voice.EnergyEndOfSpeech
import com.alfred.app.data.voice.VoiceController
import com.alfred.app.data.voice.VoicePhase
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.mapNotNull
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** What the wake word listener is doing, for the notification and Settings. */
sealed interface WakeState {
    /** Turned off in Settings, or the service isn't running. */
    data object Off : WakeState

    /** Waiting for [model]'s wake word. */
    data class Listening(val model: StockWakeWord) : WakeState

    /** A voice turn (wake word or push-to-talk) is using the mic. */
    data object InTurn : WakeState

    data class Paused(val reason: PauseReason) : WakeState

    /** The mic or the model failed; it tries again shortly. */
    data class Failed(val message: String) : WakeState
}

/** The state in a few words, for the notification and Settings. */
fun WakeState.describe(): String = when (this) {
    is WakeState.Listening -> "Listening for “${model.label}”"
    WakeState.InTurn -> "Talking to Alfred"
    is WakeState.Paused -> reason.text
    is WakeState.Failed -> message
    WakeState.Off -> "Off"
}

enum class PauseReason(val text: String) {
    User("Paused"),
    Call("Paused during a call"),
    MicInUse("Paused: another app is using the microphone"),
    DoNotDisturb("Paused while Do Not Disturb is on"),
}

/** Phone state that pauses listening (ADR 0019). Each flow says whether its reason applies now. */
interface WakeSignals {
    /** Another app is recording (ours excluded). Android would give us silence anyway. */
    val otherAppRecording: Flow<Boolean>

    /** A phone or VoIP call is going on. */
    val inCall: Flow<Boolean>

    val doNotDisturb: Flow<Boolean>
}

/** What the user notices on a wake: a chime (if on) and a light buzz. Returns once the chime is done. */
fun interface WakeFeedback {
    suspend fun onWake(chime: Boolean)
}

/** A wake that just happened, while "That wasn't me" can still report it. */
data class RecentWake(val atMillis: Long, val model: StockWakeWord, val score: Float)

/**
 * The wake word loop (ADR 0019), shared by the foreground service and Settings. While it's
 * enabled, not paused and no turn is running, it streams the mic into a [WakeWordDetector]. On a
 * wake it closes the mic, plays the chime, and starts a hands-free turn on the
 * [VoiceController] (which opens the mic again); listening resumes when the turn is over.
 * [start] it from the main thread; [scope] should be main-bound.
 */
class WakeWordController(
    private val settingsRepository: WakeWordSettingsRepository,
    private val voice: VoiceController,
    private val capture: AudioCapture,
    private val signals: WakeSignals,
    private val feedback: WakeFeedback,
    private val events: WakeEventLog,
    private val loadModels: (StockWakeWord) -> WakeWordModels,
    private val scope: CoroutineScope,
    private val workDispatcher: CoroutineDispatcher = Dispatchers.Default,
    /** Wall time, for event timestamps. */
    private val clock: () -> Long = System::currentTimeMillis,
    /** Monotonic time, for the detector's cooldown (wall time can jump). */
    private val monotonicClock: () -> Long = { System.nanoTime() / 1_000_000 },
    private val endOfSpeech: () -> EndOfSpeechDetector = { EnergyEndOfSpeech() },
    private val retryMillis: Long = RETRY_MILLIS,
    private val falseAcceptWindowMillis: Long = FALSE_ACCEPT_WINDOW_MILLIS,
) {
    private val _state = MutableStateFlow<WakeState>(WakeState.Off)
    val state: StateFlow<WakeState> = _state.asStateFlow()

    private val _recentWake = MutableStateFlow<RecentWake?>(null)

    /** The last wake, for [falseAcceptWindowMillis] after it (the notification's "That wasn't me"). */
    val recentWake: StateFlow<RecentWake?> = _recentWake.asStateFlow()

    private val userPaused = MutableStateFlow(false)
    private var job: Job? = null
    private var clearWakeJob: Job? = null

    /**
     * The detector for one model, kept while that model stays selected. Owned by the loop:
     * set on [workDispatcher] as soon as a model loads (so a cancelled load isn't lost) and
     * closed in the loop's `finally`, once nothing can still be running inference on it.
     */
    @Volatile
    private var loaded: Pair<StockWakeWord, Loaded>? = null

    private class Loaded(val models: WakeWordModels, val detector: WakeWordDetector)

    private sealed interface Mode {
        data class Idle(val state: WakeState) : Mode

        data class Listen(val settings: WakeWordSettings) : Mode
    }

    /** Starts listening (as settings and phone state allow) until [stop]. Idempotent. */
    fun start() {
        if (job?.isActive == true) return
        // A stopped loop may still be finishing a chunk and closing its models: wait for it.
        val previous = job
        job = scope.launch {
            previous?.join()
            try {
                modes().collectLatest { mode ->
                    when (mode) {
                        is Mode.Idle -> _state.value = mode.state
                        is Mode.Listen -> listen(mode.settings)
                    }
                }
            } finally {
                _state.value = WakeState.Off
                // Runs only after any inference in progress has returned (withContext waits for
                // its block), so the native sessions are never closed while in use.
                val models = loaded?.second?.models
                loaded = null
                if (models != null) withContext(NonCancellable + workDispatcher) { models.close() }
            }
        }
    }

    /** Stops listening. The models are closed as soon as the chunk being scored (if any) is done. */
    fun stop() {
        job?.cancel()
        _state.value = WakeState.Off
    }

    fun pause() {
        userPaused.value = true
    }

    fun resume() {
        userPaused.value = false
    }

    /** "That wasn't me": logs the last wake as a false accept and stops the turn it started. */
    fun reportFalseAccept() {
        val wake = _recentWake.value ?: return
        _recentWake.value = null
        clearWakeJob?.cancel()
        voice.cancel()
        scope.launch {
            events.record(WakeEvent(WakeEvent.Kind.FalseAccept, clock(), wake.model, wake.score))
        }
    }

    private fun modes(): Flow<Mode> {
        val phone = combine(signals.inCall, signals.otherAppRecording, signals.doNotDisturb) { call, mic, dnd ->
            Triple(call, mic, dnd)
        }
        val phase = voice.state.map { it.phase }.distinctUntilChanged()
        return combine(settingsRepository.settings, userPaused, phase, phone) { settings, paused, voicePhase, p ->
            val (call, micInUse, dnd) = p
            when {
                !settings.enabled -> Mode.Idle(WakeState.Off)
                paused -> Mode.Idle(WakeState.Paused(PauseReason.User))
                voicePhase != VoicePhase.Idle -> Mode.Idle(WakeState.InTurn)
                call -> Mode.Idle(WakeState.Paused(PauseReason.Call))
                micInUse -> Mode.Idle(WakeState.Paused(PauseReason.MicInUse))
                dnd && settings.pauseInDoNotDisturb -> Mode.Idle(WakeState.Paused(PauseReason.DoNotDisturb))
                else -> Mode.Listen(settings)
            }
        }.distinctUntilChanged()
    }

    /** Listens until cancelled (the mode changed) or a turn starts (which changes the mode). */
    private suspend fun listen(settings: WakeWordSettings) {
        while (true) {
            val detection = try {
                val detector = detectorFor(settings.model)
                detector.threshold = settings.threshold
                _state.value = WakeState.Listening(settings.model)
                withContext(workDispatcher) {
                    detector.reset()
                    // first() ends the collection, which closes the mic before we go on.
                    capture.frames().mapNotNull(detector::process).first()
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.value = WakeState.Failed(describe(e))
                delay(retryMillis)
                continue
            } catch (e: LinkageError) {
                // ONNX Runtime's native library isn't built for this device's ABI.
                _state.value = WakeState.Failed("The wake word engine doesn't run on this device")
                return
            }
            onWake(settings.model, detection)
            feedback.onWake(settings.chime)
            if (voice.startListening(endOfSpeech())) {
                // The voice phase leaves Idle, so the mode changes and this is cancelled.
                awaitCancellation()
            }
            // No connection: the voice state shows why. Go back to listening.
        }
    }

    private suspend fun detectorFor(model: StockWakeWord): WakeWordDetector {
        loaded?.let { (current, value) -> if (current == model) return value.detector }
        // Nothing else uses the old models now: collectLatest waits for the previous listen().
        return withContext(workDispatcher) {
            loaded?.second?.models?.close()
            loaded = null
            val models = loadModels(model)
            val value = Loaded(models, WakeWordDetector(models, clock = monotonicClock))
            // Recorded here, on the worker, so the loop's finally closes it even if we're cancelled.
            loaded = model to value
            value.detector
        }
    }

    private fun onWake(model: StockWakeWord, detection: WakeDetection) {
        val wake = RecentWake(clock(), model, detection.score)
        _recentWake.value = wake
        clearWakeJob?.cancel()
        clearWakeJob = scope.launch {
            delay(falseAcceptWindowMillis)
            if (_recentWake.value == wake) _recentWake.value = null
        }
        scope.launch { events.record(WakeEvent(WakeEvent.Kind.Detection, wake.atMillis, model, wake.score)) }
    }

    private fun describe(e: Exception): String = when (e) {
        is SecurityException -> "Alfred needs microphone permission"
        else -> e.message ?: "The wake word listener failed"
    }

    companion object {
        const val RETRY_MILLIS = 5_000L
        const val FALSE_ACCEPT_WINDOW_MILLIS = 30_000L
    }
}
