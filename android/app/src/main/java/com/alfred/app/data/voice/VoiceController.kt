package com.alfred.app.data.voice

import com.alfred.app.data.device.ApprovalSigner
import com.alfred.app.data.server.AnswerResult
import com.alfred.app.data.server.ServerClient
import com.alfred.app.data.settings.SettingsRepository
import java.time.OffsetDateTime
import java.time.format.DateTimeParseException
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class VoicePhase {
    Idle,

    /** The mic is open and streaming. */
    Listening,

    /** Speech ended; waiting for the transcript and reply. */
    Thinking,

    /** Playing the reply. */
    Speaking,
}

/** A `confirm.request` waiting for the Approve / Deny sheet (or a spoken yes/no). */
data class PendingConfirmation(
    val id: String,
    val summary: String,
    /** Only a paired app can approve these, over HTTP with a signed proof (ADR 0016). */
    val irreversible: Boolean,
    /** Epoch millis, or null if the server didn't say. */
    val expiresAtMillis: Long?,
    /** Set while an answer is on its way, so the buttons can't be pressed twice. */
    val answering: Boolean = false,
)

data class VoiceState(
    val phase: VoicePhase = VoicePhase.Idle,
    val transcript: String = "",
    val reply: String = "",
    val metadata: TurnMetadata? = null,
    val confirmation: PendingConfirmation? = null,
    /** Something went wrong with the last turn or answer, in words for the user. */
    val error: String? = null,
)

/**
 * Runs voice turns over the [DeviceConnection] (docs/protocol.md, "A turn"): mic audio up,
 * transcript, reply text and reply audio down. One turn at a time. Shared by push-to-talk and,
 * later, the wake-word service. Call it from the main thread; [scope] should be main-bound.
 */
class VoiceController(
    private val connection: DeviceConnection,
    private val capture: AudioCapture,
    private val player: AudioPlayer,
    private val serverClient: ServerClient,
    private val settingsRepository: SettingsRepository,
    private val signer: ApprovalSigner,
    private val scope: CoroutineScope,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val _state = MutableStateFlow(VoiceState())
    val state: StateFlow<VoiceState> = _state.asStateFlow()

    /** The turn whose messages and audio we take; anything for another turn is dropped. */
    private var turnId: String? = null

    /** The server has sent the current turn's last message. */
    private var serverDone = true
    private var captureJob: Job? = null
    private var expiryJob: Job? = null

    /** A `confirm.reply` held back until the turn that asked has ended (else it's `busy`). */
    private var deferredReply: ClientMessage.ConfirmReply? = null

    /** The confirmation [deferredReply] answers; the sheet comes back if the reply can't be sent. */
    private var deferredFor: PendingConfirmation? = null

    init {
        scope.launch { connection.events.collect(::onEvent) }
        scope.launch {
            player.playing.collect { playing ->
                if (!playing && serverDone && _state.value.phase == VoicePhase.Speaking) {
                    _state.update { it.copy(phase = VoicePhase.Idle) }
                }
            }
        }
    }

    /** Opens the mic and starts a turn. False if there's no usable connection. */
    fun startListening(): Boolean {
        if (_state.value.phase == VoicePhase.Listening) return true
        val link = connection.state.value
        if (link !is LinkState.Connected) {
            connection.reconnectNow()
            _state.update { it.copy(error = "Not connected to the server") }
            return false
        }
        if (!link.voice) {
            _state.update { it.copy(error = "Voice is turned off on the server") }
            return false
        }
        abandonTurn()
        val id = newTurnId()
        if (!connection.send(ClientMessage.TurnStart(id))) {
            _state.update { it.copy(error = "Not connected to the server") }
            return false
        }
        turnId = id
        serverDone = false
        deferredReply = null
        deferredFor = null
        expiryJob?.cancel()
        // A new turn answers (or replaces) any pending confirmation on the server's side.
        _state.value = VoiceState(phase = VoicePhase.Listening)
        captureJob = scope.launch {
            try {
                capture.frames().collect { connection.sendAudio(it) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (turnId == id) failTurn(micError(e), cancelOnServer = true)
            }
        }
        return true
    }

    /** End of speech: the server transcribes and answers. */
    fun stopListening() {
        if (_state.value.phase != VoicePhase.Listening) return
        captureJob?.cancel()
        captureJob = null
        turnId?.let { connection.send(ClientMessage.TurnEnd(it)) }
        _state.update { it.copy(phase = VoicePhase.Thinking) }
    }

    /** Stops whatever is happening: listening, thinking or speaking. Text stays on screen. */
    fun cancel() {
        abandonTurn()
        _state.update { it.copy(phase = VoicePhase.Idle) }
        sendDeferredReply()
    }

    /** Answers the pending confirmation from the on-screen buttons. */
    fun answer(approve: Boolean) {
        val confirmation = _state.value.confirmation ?: return
        if (confirmation.answering) return
        if (confirmation.irreversible) {
            answerOverHttp(confirmation, approve)
        } else {
            answerOverSocket(confirmation, approve)
        }
    }

    /** Hides the sheet without answering; the server lets it expire (or a spoken answer settles it). */
    fun dismissConfirmation() {
        expiryJob?.cancel()
        _state.update { it.copy(confirmation = null) }
    }

    /**
     * The sheet stays up (buttons disabled) until the `confirm.reply` has actually been sent, so
     * an answer that can't go out comes back to the sheet to retry instead of vanishing.
     */
    private fun answerOverSocket(confirmation: PendingConfirmation, approve: Boolean) {
        val reply = ClientMessage.ConfirmReply(confirmation.id, approve, newTurnId())
        _state.update { it.copy(confirmation = confirmation.copy(answering = true), error = null) }
        player.stop()
        if (turnId != null && !serverDone) {
            // The asking turn hasn't finished on the server yet: quiet it, send when it ends.
            deferredReply = reply
            deferredFor = confirmation
            return
        }
        sendReply(reply, confirmation)
    }

    private fun sendDeferredReply() {
        val reply = deferredReply ?: return
        val confirmation = deferredFor ?: return
        deferredReply = null
        deferredFor = null
        sendReply(reply, confirmation)
    }

    private fun sendReply(reply: ClientMessage.ConfirmReply, confirmation: PendingConfirmation) {
        if (!connection.send(reply)) {
            restoreConfirmation(confirmation, "Not connected to the server; try again")
            return
        }
        expiryJob?.cancel()
        // Its spoken answer is a turn of its own, tagged with our turn id.
        turnId = reply.turnId
        serverDone = false
        _state.update {
            it.copy(
                phase = VoicePhase.Thinking,
                transcript = "",
                reply = "",
                metadata = null,
                confirmation = null,
                error = null,
            )
        }
    }

    /** Puts the sheet back (if it hasn't expired meanwhile) with [error], ready to answer again. */
    private fun restoreConfirmation(confirmation: PendingConfirmation, error: String) {
        _state.update {
            val current = it.confirmation
            if (current == null || current.id != confirmation.id) return@update it.copy(error = error)
            it.copy(confirmation = confirmation.copy(answering = false), error = error)
        }
    }

    private fun answerOverHttp(confirmation: PendingConfirmation, approve: Boolean) {
        _state.update { it.copy(confirmation = confirmation.copy(answering = true), error = null) }
        scope.launch {
            val settings = settingsRepository.settings.first()
            val deviceId = settings.pairedDeviceId
            if (approve && deviceId == null) {
                _state.update {
                    it.copy(
                        confirmation = confirmation,
                        error = "Pair this phone in Settings → Approvals to approve this",
                    )
                }
                return@launch
            }
            val result = try {
                val proof = if (approve && deviceId != null) {
                    withContext(ioDispatcher) { signer.sign(deviceId, confirmation.id, approve = true) }
                } else {
                    null
                }
                serverClient.answerConfirmation(settings, confirmation.id, approve, proof)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                AnswerResult.Failed(e.message ?: e.javaClass.simpleName)
            }
            when (result) {
                is AnswerResult.Answered -> {
                    expiryJob?.cancel()
                    val reply = result.reply ?: if (approve) "Done." else "OK, I won't."
                    _state.update {
                        it.copy(confirmation = null, transcript = "", reply = reply, metadata = null)
                    }
                }
                is AnswerResult.Gone -> {
                    expiryJob?.cancel()
                    _state.update { it.copy(confirmation = null, error = "That request has expired") }
                }
                is AnswerResult.Forbidden ->
                    _state.update { it.copy(confirmation = confirmation, error = result.message) }
                is AnswerResult.Failed ->
                    _state.update { it.copy(confirmation = confirmation, error = result.reason) }
            }
        }
    }

    private fun onEvent(event: DeviceEvent) {
        when (event) {
            is DeviceEvent.Message -> onMessage(event.message)
            is DeviceEvent.Audio -> if (event.audio.turnId == turnId) player.write(event.audio.pcm)
            is DeviceEvent.Disconnected -> {
                deferredFor?.let { restoreConfirmation(it, "Connection lost; try again once reconnected") }
                deferredReply = null
                deferredFor = null
                if (_state.value.phase != VoicePhase.Idle) failTurn("Connection lost", cancelOnServer = false)
            }
        }
    }

    private fun onMessage(message: ServerMessage) {
        when (message) {
            is ServerMessage.ConfirmRequest -> showConfirmation(message)
            is ServerMessage.TranscriptFinal -> ifCurrent(message.turnId) {
                _state.update { it.copy(transcript = message.text) }
            }
            is ServerMessage.ReplyTextDelta -> ifCurrent(message.turnId) {
                _state.update { it.copy(reply = it.reply + message.text) }
            }
            is ServerMessage.ReplyAudioStart -> ifCurrent(message.turnId) {
                player.begin(message.sampleRate)
                _state.update { if (it.phase == VoicePhase.Thinking) it.copy(phase = VoicePhase.Speaking) else it }
            }
            is ServerMessage.Metadata -> ifCurrent(message.metadata.turnId) {
                _state.update { it.copy(metadata = message.metadata) }
            }
            is ServerMessage.ReplyAudioEnd -> ifCurrent(message.turnId) {
                serverDone = true
                player.finish()
                if (_state.value.phase == VoicePhase.Thinking || !player.playing.value) {
                    _state.update { it.copy(phase = VoicePhase.Idle) }
                }
                sendDeferredReply()
            }
            is ServerMessage.TurnCancelled -> ifCurrent(message.turnId) {
                serverDone = true
                _state.update { it.copy(phase = VoicePhase.Idle) }
                sendDeferredReply()
            }
            is ServerMessage.Error -> onError(message)
            is ServerMessage.HelloOk, is ServerMessage.Pong, is ServerMessage.Unknown -> Unit
        }
    }

    private fun onError(error: ServerMessage.Error) {
        val text = describe(error)
        when {
            error.turnId == null -> _state.update { it.copy(error = text) }
            error.turnId == turnId -> {
                serverDone = true
                captureJob?.cancel()
                captureJob = null
                // Audio that already arrived (e.g. before `tts_failed`) still plays out.
                player.finish()
                _state.update {
                    val phase = if (it.phase == VoicePhase.Speaking) it.phase else VoicePhase.Idle
                    it.copy(phase = phase, error = text)
                }
                sendDeferredReply()
            }
            // A confirm.reply answer held back behind this turn may have failed too: nothing to do.
        }
    }

    private fun showConfirmation(request: ServerMessage.ConfirmRequest) {
        val expiresAt = request.expiresAt?.let(::parseInstant)
        val confirmation = PendingConfirmation(request.id, request.summary, request.irreversible, expiresAt)
        _state.update { it.copy(confirmation = confirmation) }
        expiryJob?.cancel()
        if (expiresAt != null) {
            expiryJob = scope.launch {
                delay((expiresAt - clock()).coerceAtLeast(0))
                _state.update { if (it.confirmation?.id == request.id) it.copy(confirmation = null) else it }
            }
        }
    }

    private inline fun ifCurrent(id: String, block: () -> Unit) {
        if (id == turnId) block()
    }

    /** Drops the current turn: mic off, playback stopped, server told if it's still running. */
    private fun abandonTurn() {
        captureJob?.cancel()
        captureJob = null
        if (player.playing.value) player.stop()
        val id = turnId
        if (id != null && !serverDone) connection.send(ClientMessage.TurnCancel(id))
        turnId = null
        serverDone = true
    }

    private fun failTurn(message: String, cancelOnServer: Boolean) {
        if (cancelOnServer) {
            abandonTurn()
        } else {
            captureJob?.cancel()
            captureJob = null
            player.stop()
            turnId = null
            serverDone = true
        }
        _state.update { it.copy(phase = VoicePhase.Idle, error = message) }
    }

    private fun micError(e: Exception): String = when (e) {
        is SecurityException -> "Alfred needs microphone permission"
        else -> e.message ?: "The microphone failed"
    }

    private fun describe(error: ServerMessage.Error): String = when (error.code) {
        "no_speech" -> "Didn't catch that"
        "voice_unavailable" -> "Voice is turned off on the server"
        "audio_too_long" -> "That was too long; try a shorter request"
        "busy" -> "Alfred is still busy with the last request"
        "unknown_confirmation", "confirmation_closed" -> "That request has already been answered or expired"
        else -> error.message.ifBlank { error.code }
    }

    private fun parseInstant(text: String): Long? = try {
        OffsetDateTime.parse(text).toInstant().toEpochMilli()
    } catch (e: DateTimeParseException) {
        null
    }
}
