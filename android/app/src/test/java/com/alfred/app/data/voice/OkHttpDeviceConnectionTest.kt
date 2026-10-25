package com.alfred.app.data.voice

import com.alfred.app.data.settings.AppSettings
import com.alfred.app.testing.FakeSettingsRepository
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** Against a real WebSocket (MockWebServer) playing the server's side of docs/protocol.md. */
class OkHttpDeviceConnectionTest {

    private val server = MockWebServer()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val settings = FakeSettingsRepository(AppSettings("", "token"))
    private lateinit var connection: OkHttpDeviceConnection

    /** The server end of one connection. */
    private class ServerSide : WebSocketListener() {
        val received = Channel<String>(Channel.UNLIMITED)
        val binary = Channel<ByteString>(Channel.UNLIMITED)
        val opened = Channel<WebSocket>(Channel.UNLIMITED)

        override fun onOpen(webSocket: WebSocket, response: Response) {
            opened.trySend(webSocket)
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            received.trySend(text)
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            binary.trySend(bytes)
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(1000, null)
        }
    }

    @Before
    fun setUp() {
        server.start()
        runBlocking { settings.save(AppSettings(server.url("/").toString(), "token")) }
        connection = OkHttpDeviceConnection(
            client = OkHttpClient(),
            settingsRepository = settings,
            deviceIds = { "device-1" },
            appVersion = "test",
            scope = scope,
            retryDelay = { 50L },
            pingIntervalMillis = 100L,
            helloTimeoutMillis = 2_000L,
        )
    }

    @After
    fun tearDown() {
        connection.stop()
        scope.cancel()
        server.close()
    }

    private fun upgrade(): ServerSide = ServerSide().also {
        server.enqueue(MockResponse.Builder().webSocketUpgrade(it).build())
    }

    private suspend fun ServerSide.handshake(sessionId: String = "s-1"): WebSocket {
        val ws = opened.receive()
        val hello = JSONObject(received.receive())
        assertEquals("hello", hello.getString("type"))
        ws.send("""{"type":"hello.ok","protocol":1,"session_id":"$sessionId","voice":true}""")
        return ws
    }

    private suspend fun awaitState(predicate: (LinkState) -> Boolean): LinkState =
        withTimeout(5_000) { connection.state.first(predicate) }

    @Test
    fun `sends the token and hello, then is connected`() = runBlocking<Unit> {
        val side = upgrade()
        connection.start()

        val ws = side.opened.receive()
        val hello = JSONObject(withTimeout(5_000) { side.received.receive() })
        assertEquals(1, hello.getInt("protocol"))
        assertEquals("device-1", hello.getString("device_id"))
        assertEquals("phone", hello.getString("device_type"))
        assertEquals("test", hello.getString("app_version"))
        assertFalse("the token goes in the header, not hello", hello.has("token"))
        val request = server.takeRequest(5, TimeUnit.SECONDS)!!
        assertEquals("Bearer token", request.headers["Authorization"])
        assertEquals("/ws/device", request.url.encodedPath)
        assertFalse(connection.send(ClientMessage.Ping()))

        ws.send("""{"type":"hello.ok","protocol":1,"session_id":"s-1","voice":true}""")

        assertEquals(LinkState.Connected("s-1", voice = true), awaitState { it is LinkState.Connected })
    }

    @Test
    fun `forwards messages and reply audio, and sends client frames`() = runBlocking<Unit> {
        val side = upgrade()
        connection.start()
        val events = async { withTimeout(5_000) { connection.events.first { it is DeviceEvent.Audio } } }
        val transcript = async {
            withTimeout(5_000) {
                connection.events.first {
                    it is DeviceEvent.Message && it.message is ServerMessage.TranscriptFinal
                }
            }
        }
        val ws = side.handshake()
        awaitState { it is LinkState.Connected }

        assertTrue(connection.send(ClientMessage.TurnStart("t1")))
        assertEquals("turn.start", JSONObject(side.received.receive()).getString("type"))
        assertTrue(connection.sendAudio(byteArrayOf(9, 8)))
        assertArrayEquals(byteArrayOf(9, 8), side.binary.receive().toByteArray())

        ws.send("""{"type":"transcript.final","turn_id":"t1","text":"hi"}""")
        ws.send((byteArrayOf(0x01, 2) + "t1".toByteArray() + byteArrayOf(5, 6)).toByteString())

        val message = transcript.await() as DeviceEvent.Message
        assertEquals(ServerMessage.TranscriptFinal("t1", "hi"), message.message)
        val audio = (events.await() as DeviceEvent.Audio).audio
        assertEquals("t1", audio.turnId)
        assertArrayEquals(byteArrayOf(5, 6), audio.pcm)
    }

    @Test
    fun `pings to keep the server's idle timer happy`() = runBlocking<Unit> {
        val side = upgrade()
        connection.start()
        side.handshake()

        val ping = JSONObject(withTimeout(5_000) { side.received.receive() })
        assertEquals("ping", ping.getString("type"))
    }

    @Test
    fun `reconnects after the server drops the connection`() = runBlocking<Unit> {
        val first = upgrade()
        val second = upgrade()
        connection.start()
        val disconnected = async {
            withTimeout(5_000) { connection.events.first { it is DeviceEvent.Disconnected } }
        }
        val ws = first.handshake("s-1")
        awaitState { it is LinkState.Connected }

        ws.close(1011, "restarting")

        disconnected.await()
        second.handshake("s-1")
        assertEquals(LinkState.Connected("s-1", voice = true), awaitState { it is LinkState.Connected })
        assertEquals(2, server.requestCount)
    }

    @Test
    fun `waits and retries when the server is unreachable`() = runBlocking<Unit> {
        server.enqueue(MockResponse.Builder().code(500).build())
        val side = upgrade()
        connection.start()

        val waiting = awaitState { it is LinkState.Waiting } as LinkState.Waiting
        assertEquals(50L, waiting.retryInMillis)
        side.handshake()
        awaitState { it is LinkState.Connected }
    }

    @Test
    fun `a rejected token stops retrying until asked`() = runBlocking<Unit> {
        val side = upgrade()
        connection.start()
        side.opened.receive().close(CloseCode.UNAUTHORISED, "bad token")

        assertEquals(
            LinkState.Rejected("The server rejected the API token"),
            awaitState { it is LinkState.Rejected },
        )
        delay(300)
        assertEquals(1, server.requestCount)

        val again = upgrade()
        connection.reconnectNow()
        again.handshake()
        awaitState { it is LinkState.Connected }
    }

    @Test
    fun `new settings reconnect`() = runBlocking<Unit> {
        val first = upgrade()
        val second = upgrade()
        connection.start()
        first.handshake()
        awaitState { it is LinkState.Connected }

        settings.save(AppSettings(server.url("/").toString(), "new-token"))

        second.handshake()
        server.takeRequest(5, TimeUnit.SECONDS)
        val request = server.takeRequest(5, TimeUnit.SECONDS)!!
        assertEquals("Bearer new-token", request.headers["Authorization"])
    }

    @Test
    fun `not configured without a token`() = runBlocking<Unit> {
        settings.save(AppSettings(server.url("/").toString(), ""))
        connection.start()

        assertEquals(LinkState.NotConfigured, awaitState { it == LinkState.NotConfigured })
    }

    @Test
    fun `stop goes offline`() = runBlocking<Unit> {
        val side = upgrade()
        connection.start()
        side.handshake()
        awaitState { it is LinkState.Connected }

        connection.stop()

        assertEquals(LinkState.Offline, connection.state.value)
        assertFalse(connection.send(ClientMessage.Ping()))
    }
}
