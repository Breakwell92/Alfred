package com.alfred.app.data.voice

import com.alfred.app.data.settings.SettingsRepository
import com.alfred.app.data.settings.isValidApiToken
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString

/** The state of the device WebSocket, for the UI. */
sealed interface LinkState {
    /** Not started (the app is in the background). */
    data object Offline : LinkState

    /** No server URL or token saved yet. */
    data object NotConfigured : LinkState

    data object Connecting : LinkState

    /** `hello.ok` received. [voice] is false when the server has STT or TTS off. */
    data class Connected(val sessionId: String, val voice: Boolean) : LinkState

    /** The connection dropped or couldn't be made; retrying after [retryInMillis]. */
    data class Waiting(val reason: String, val retryInMillis: Long) : LinkState

    /** The server refused us (token, protocol); no retries until settings change or [DeviceConnection.reconnectNow]. */
    data class Rejected(val reason: String) : LinkState
}

/** Everything that comes down the socket, in order. */
sealed interface DeviceEvent {
    data class Message(val message: ServerMessage) : DeviceEvent

    class Audio(val audio: ReplyAudio) : DeviceEvent

    /** A connected socket went away; any turn in progress on it is lost. */
    data class Disconnected(val reason: String) : DeviceEvent
}

/**
 * One WebSocket to the server's `/ws/device` (docs/protocol.md), shared by everything that
 * talks to it: push-to-talk now, the wake-word service later.
 */
interface DeviceConnection {
    val state: StateFlow<LinkState>
    val events: SharedFlow<DeviceEvent>

    /** Connects (and keeps reconnecting) until [stop]. Idempotent. */
    fun start()

    fun stop()

    /** Retry now instead of waiting out the backoff, e.g. when the user presses Talk. */
    fun reconnectNow()

    /** False when there's no connected socket (the message is dropped). */
    fun send(message: ClientMessage): Boolean

    /** Sends one binary frame of 16 kHz PCM16 microphone audio. */
    fun sendAudio(pcm: ByteArray): Boolean
}

/** Delay before reconnect attempt [attempt] (0-based): 1, 2, 4, 8, then 15 s. */
fun defaultRetryDelay(attempt: Int): Long = minOf(1_000L shl minOf(attempt, 4), 15_000L)

/** `http://glados:8000` → `http://glados:8000/ws/device` (OkHttp upgrades http(s) to ws(s)). */
fun webSocketUrl(serverUrl: String): HttpUrl? {
    val base = serverUrl.trim().toHttpUrlOrNull() ?: return null
    val builder = base.newBuilder()
    if (base.pathSegments.lastOrNull() == "") builder.removePathSegment(base.pathSize - 1)
    return builder.addPathSegment("ws").addPathSegment("device").build()
}

class OkHttpDeviceConnection(
    client: OkHttpClient,
    private val settingsRepository: SettingsRepository,
    private val deviceIds: DeviceIdRepository,
    private val appVersion: String,
    private val scope: CoroutineScope,
    private val retryDelay: (attempt: Int) -> Long = ::defaultRetryDelay,
    private val pingIntervalMillis: Long = PING_INTERVAL_MILLIS,
    private val helloTimeoutMillis: Long = HELLO_TIMEOUT_MILLIS,
) : DeviceConnection {

    // No read timeout on a long-lived socket; WebSocket pings spot a dead link (a phone that
    // changed network without the TCP connection noticing) and fail it.
    private val wsClient = client.newBuilder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(WS_PING_INTERVAL_SECONDS, TimeUnit.SECONDS)
        .build()

    private val _state = MutableStateFlow<LinkState>(LinkState.Offline)
    override val state: StateFlow<LinkState> = _state.asStateFlow()

    private val _events = MutableSharedFlow<DeviceEvent>(extraBufferCapacity = EVENT_BUFFER)
    override val events: SharedFlow<DeviceEvent> = _events.asSharedFlow()

    private val wake = Channel<Unit>(Channel.CONFLATED)
    private var job: Job? = null

    /** The socket once `hello.ok` has arrived; null otherwise. */
    @Volatile
    private var socket: WebSocket? = null

    @Synchronized
    override fun start() {
        if (job?.isActive == true) return
        job = scope.launch { run() }
    }

    @Synchronized
    override fun stop() {
        job?.cancel()
        job = null
        socket = null
        _state.value = LinkState.Offline
    }

    override fun reconnectNow() {
        wake.trySend(Unit)
    }

    override fun send(message: ClientMessage): Boolean = socket?.send(message.encode()) ?: false

    override fun sendAudio(pcm: ByteArray): Boolean = socket?.send(pcm.toByteString()) ?: false

    private suspend fun run() {
        settingsRepository.settings
            .map { Target(it.serverUrl.trim(), it.apiToken.trim()) }
            .distinctUntilChanged()
            .collectLatest(::connectLoop)
    }

    private data class Target(val serverUrl: String, val token: String)

    private suspend fun connectLoop(target: Target) {
        if (target.serverUrl.isEmpty() || target.token.isEmpty()) {
            _state.value = LinkState.NotConfigured
            awaitCancellation()
        }
        val url = webSocketUrl(target.serverUrl)
        val problem = when {
            url == null -> "Invalid server URL"
            !isValidApiToken(target.token) -> "The saved API token is invalid"
            else -> null
        }
        if (problem != null || url == null) {
            _state.value = LinkState.Rejected(problem ?: "Invalid server URL")
            awaitCancellation()
        }
        var attempt = 0
        while (true) {
            _state.value = LinkState.Connecting
            when (val outcome = session(url, target.token)) {
                is Outcome.Refused -> {
                    _state.value = LinkState.Rejected(outcome.reason)
                    wake.receive()
                    attempt = 0
                }
                is Outcome.Lost -> {
                    if (outcome.wasConnected) attempt = 0
                    val wait = retryDelay(attempt++)
                    _state.value = LinkState.Waiting(outcome.reason, wait)
                    withTimeoutOrNull(wait) { wake.receive() }
                }
            }
        }
    }

    private sealed interface Outcome {
        data class Lost(val reason: String, val wasConnected: Boolean) : Outcome

        data class Refused(val reason: String) : Outcome
    }

    private sealed interface SocketEvent {
        data object Opened : SocketEvent

        data class Text(val text: String) : SocketEvent

        class Binary(val bytes: ByteString) : SocketEvent

        data class Closed(val code: Int, val reason: String) : SocketEvent

        data class Failed(val error: Throwable, val httpCode: Int?) : SocketEvent
    }

    /** One connection, from upgrade to close. Suspends until it's over. */
    private suspend fun session(url: HttpUrl, token: String): Outcome = coroutineScope {
        val inbox = Channel<SocketEvent>(Channel.UNLIMITED)
        val request = Request.Builder().url(url).header("Authorization", "Bearer $token").build()
        val ws = wsClient.newWebSocket(
            request,
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    inbox.trySend(SocketEvent.Opened)
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    inbox.trySend(SocketEvent.Text(text))
                }

                override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                    inbox.trySend(SocketEvent.Binary(bytes))
                }

                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    webSocket.close(CloseCode.NORMAL, null)
                    inbox.trySend(SocketEvent.Closed(code, reason))
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    inbox.trySend(SocketEvent.Failed(t, response?.code))
                }
            },
        )
        var connected = false
        val helloTimer = launch {
            delay(helloTimeoutMillis)
            inbox.trySend(SocketEvent.Failed(Exception("No answer to hello"), null))
        }
        var pinger: Job? = null
        try {
            for (event in inbox) {
                when (event) {
                    SocketEvent.Opened -> {
                        val hello = ClientMessage.Hello(
                            deviceId = deviceIds.deviceId(),
                            appVersion = appVersion,
                            capabilities = CAPABILITIES,
                        )
                        ws.send(hello.encode())
                    }
                    is SocketEvent.Text -> {
                        val message = try {
                            parseServerMessage(event.text)
                        } catch (e: ProtocolException) {
                            continue // A frame we can't read; the protocol says carry on.
                        }
                        if (!connected) {
                            if (message !is ServerMessage.HelloOk) continue
                            connected = true
                            helloTimer.cancel()
                            socket = ws
                            _state.value = LinkState.Connected(message.sessionId, message.voice)
                            pinger = launch { ping(ws) }
                        }
                        _events.emit(DeviceEvent.Message(message))
                    }
                    is SocketEvent.Binary -> {
                        if (!connected) continue
                        val audio = try {
                            parseAudioFrame(event.bytes.toByteArray())
                        } catch (e: ProtocolException) {
                            continue
                        }
                        _events.emit(DeviceEvent.Audio(audio))
                    }
                    is SocketEvent.Closed -> return@coroutineScope closed(event, connected)
                    is SocketEvent.Failed -> return@coroutineScope failed(event, connected)
                }
            }
            Outcome.Lost("Connection closed", connected)
        } finally {
            helloTimer.cancel()
            pinger?.cancel()
            socket = null
            ws.cancel()
            if (connected) _events.tryEmit(DeviceEvent.Disconnected("Connection lost"))
        }
    }

    private suspend fun ping(ws: WebSocket) {
        while (true) {
            delay(pingIntervalMillis)
            ws.send(ClientMessage.Ping(System.currentTimeMillis()).encode())
        }
    }

    private fun closed(event: SocketEvent.Closed, connected: Boolean): Outcome = when (event.code) {
        CloseCode.UNAUTHORISED -> Outcome.Refused("The server rejected the API token")
        CloseCode.NO_SERVER_TOKEN -> Outcome.Refused("The server has no API token set")
        CloseCode.BAD_HELLO -> Outcome.Refused("The server refused this app's hello: ${event.reason}")
        CloseCode.REPLACED -> Outcome.Lost("Replaced by another connection", connected)
        else -> Outcome.Lost("Closed by the server (${event.code})", connected)
    }

    private fun failed(event: SocketEvent.Failed, connected: Boolean): Outcome = when (event.httpCode) {
        401, 403 -> Outcome.Refused("The server rejected the API token")
        503 -> Outcome.Refused("The server has no API token set")
        else -> Outcome.Lost(event.error.message ?: event.error.javaClass.simpleName, connected)
    }

    companion object {
        /** App `ping` interval; the server closes a socket idle for 60 s. */
        const val PING_INTERVAL_MILLIS = 20_000L
        const val HELLO_TIMEOUT_MILLIS = 10_000L
        private const val WS_PING_INTERVAL_SECONDS = 15L
        private const val EVENT_BUFFER = 1_024
        val CAPABILITIES = listOf("audio_pcm16")
    }
}
