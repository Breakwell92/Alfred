package com.alfred.app.testing

import com.alfred.app.data.device.ApprovalProof
import com.alfred.app.data.device.DeviceKeys
import com.alfred.app.data.server.AnswerResult
import com.alfred.app.data.server.ConnectionStatus
import com.alfred.app.data.server.PairingResult
import com.alfred.app.data.server.ServerClient
import com.alfred.app.data.voice.AudioCapture
import com.alfred.app.data.voice.AudioPlayer
import com.alfred.app.data.voice.CaptureEffect
import com.alfred.app.data.voice.ClientMessage
import com.alfred.app.data.voice.DeviceConnection
import com.alfred.app.data.voice.DeviceEvent
import com.alfred.app.data.voice.LinkState
import com.alfred.app.data.voice.ReplyAudio
import com.alfred.app.data.voice.ServerMessage
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.settings.SettingsRepository
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.test.TestDispatcher
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.rules.TestWatcher
import org.junit.runner.Description

class FakeSettingsRepository(initial: AppSettings) : SettingsRepository {
    private val state = MutableStateFlow(initial)
    override val settings: StateFlow<AppSettings> = state

    /** Like the DataStore repository: saving the URL and token keeps the pairing. */
    override suspend fun save(settings: AppSettings) {
        state.value = settings.copy(
            pairedDeviceId = state.value.pairedDeviceId,
            pairedDeviceName = state.value.pairedDeviceName,
        )
    }

    override suspend fun savePairing(deviceId: String, name: String) {
        state.value = state.value.copy(pairedDeviceId = deviceId, pairedDeviceName = name)
    }
}

/** A plain JVM EC P-256 key standing in for the Android Keystore one. */
class FakeDeviceKeys : DeviceKeys {
    val keyPair: KeyPair = KeyPairGenerator.getInstance("EC").run {
        initialize(ECGenParameterSpec("secp256r1"))
        generateKeyPair()
    }
    var error: Exception? = null

    override fun publicKey(): ByteArray {
        error?.let { throw it }
        return keyPair.public.encoded
    }

    override fun sign(data: ByteArray): ByteArray = Signature.getInstance("SHA256withECDSA").run {
        initSign(keyPair.private)
        update(data)
        sign()
    }
}

/** Answers with [status] (or throws [error]); records every settings value it was asked to check. */
class FakeServerClient(var status: ConnectionStatus = ConnectionStatus.Connected) : ServerClient {
    val checked = mutableListOf<AppSettings>()
    var error: Exception? = null
    var pairing: PairingResult = PairingResult.Paired("device-1", "pixel")
    val paired = mutableListOf<Triple<AppSettings, String, ByteArray>>()

    override suspend fun pairDevice(
        settings: AppSettings,
        code: String,
        publicKey: ByteArray,
    ): PairingResult {
        paired += Triple(settings, code, publicKey)
        return pairing
    }

    override suspend fun answerConfirmation(
        settings: AppSettings,
        confirmationId: String,
        approve: Boolean,
        proof: ApprovalProof?,
    ): AnswerResult {
        answered += Answer(settings, confirmationId, approve, proof)
        return answer
    }

    data class Answer(
        val settings: AppSettings,
        val confirmationId: String,
        val approve: Boolean,
        val proof: ApprovalProof?,
    )

    val answered = mutableListOf<Answer>()
    var answer: AnswerResult = AnswerResult.Answered(null)

    override suspend fun checkConnection(settings: AppSettings): ConnectionStatus {
        checked += settings
        error?.let { throw it }
        return status
    }
}

/** Points `Dispatchers.Main` (used by `viewModelScope`) at a test dispatcher. */
@OptIn(ExperimentalCoroutinesApi::class)
class MainDispatcherRule(val dispatcher: TestDispatcher = UnconfinedTestDispatcher()) : TestWatcher() {
    override fun starting(description: Description) = Dispatchers.setMain(dispatcher)

    override fun finished(description: Description) = Dispatchers.resetMain()
}

/** A [DeviceConnection] that records what was sent; tests push server events with [receive]. */
class FakeDeviceConnection(
    initial: LinkState = LinkState.Connected("session-1", voice = true),
) : DeviceConnection {
    override val state = MutableStateFlow(initial)
    private val _events = MutableSharedFlow<DeviceEvent>(extraBufferCapacity = 64)
    override val events = _events
    val sent = mutableListOf<ClientMessage>()
    val audio = mutableListOf<ByteArray>()
    var started = false
    var reconnects = 0

    override fun start() {
        started = true
    }

    override fun stop() {
        started = false
    }

    override fun reconnectNow() {
        reconnects++
    }

    override fun send(message: ClientMessage): Boolean {
        if (state.value !is LinkState.Connected) return false
        sent += message
        return true
    }

    override fun sendAudio(pcm: ByteArray): Boolean {
        if (state.value !is LinkState.Connected) return false
        audio += pcm
        return true
    }

    fun receive(message: ServerMessage) {
        check(_events.tryEmit(DeviceEvent.Message(message)))
    }

    fun receiveAudio(turnId: String, pcm: ByteArray) {
        check(_events.tryEmit(DeviceEvent.Audio(ReplyAudio(turnId, pcm))))
    }

    fun disconnect() {
        state.value = LinkState.Waiting("gone", 1_000)
        check(_events.tryEmit(DeviceEvent.Disconnected("Connection lost")))
    }
}

/** A microphone that emits whatever the test sends into [frames], or fails with [error]. */
class FakeAudioCapture : AudioCapture {
    val frames = Channel<ByteArray>(Channel.UNLIMITED)
    override var activeEffects: Set<CaptureEffect> = emptySet()
    var error: Exception? = null
    var open = false

    override fun frames(): Flow<ByteArray> = kotlinx.coroutines.flow.flow {
        error?.let { throw it }
        open = true
        try {
            frames.receiveAsFlow().collect { emit(it) }
        } finally {
            open = false
        }
    }
}

/** Records calls; playback "finishes" when the test calls [finishPlaying]. Tests set [level]. */
class FakeAudioPlayer : AudioPlayer {
    override val playing = MutableStateFlow(false)
    override val level = MutableStateFlow(0f)
    val calls = mutableListOf<String>()
    val written = mutableListOf<ByteArray>()

    override fun begin(sampleRate: Int) {
        calls += "begin($sampleRate)"
        playing.value = true
    }

    override fun write(pcm: ByteArray) {
        written += pcm
    }

    override fun finish() {
        calls += "finish"
    }

    override fun stop() {
        calls += "stop"
        playing.value = false
    }

    fun finishPlaying() {
        playing.value = false
    }
}
