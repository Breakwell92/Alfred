package com.alfred.app.data.server

import com.alfred.app.data.settings.AppSettings
import kotlinx.coroutines.test.runTest
import mockwebserver3.Dispatcher
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import mockwebserver3.RecordedRequest
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class OkHttpServerClientTest {

    private val server = MockWebServer()
    private val client = OkHttpServerClient(OkHttpClient())

    @Before
    fun setUp() = server.start()

    @After
    fun tearDown() = server.close()

    /** Answers like the real server: /health is open, /me needs `Bearer $token`. */
    private fun serve(token: String?, healthCode: Int = 200) {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when (request.url.encodedPath) {
                "/health" -> MockResponse(code = healthCode, body = """{"status":"ok"}""")
                "/me" -> when {
                    token == null -> MockResponse(code = 503)
                    request.headers["Authorization"] == "Bearer $token" ->
                        MockResponse(code = 200, body = """{"ok":true}""")
                    else -> MockResponse(code = 401)
                }
                else -> MockResponse(code = 404)
            }
        }
    }

    private fun settings(token: String, url: String = server.url("/").toString()) =
        AppSettings(serverUrl = url, apiToken = token)

    @Test
    fun `right token is connected`() = runTest {
        serve(token = "good")

        assertEquals(ConnectionStatus.Connected, client.checkConnection(settings("good")))
    }

    @Test
    fun `url without trailing slash works`() = runTest {
        serve(token = "good")
        val url = server.url("/").toString().trimEnd('/')

        assertEquals(ConnectionStatus.Connected, client.checkConnection(settings("good", url)))
    }

    @Test
    fun `wrong token is unauthorised`() = runTest {
        serve(token = "good")

        assertEquals(ConnectionStatus.Unauthorised, client.checkConnection(settings("bad")))
    }

    @Test
    fun `server without a configured token is a server error`() = runTest {
        serve(token = null)

        assertEquals(ConnectionStatus.ServerError(503), client.checkConnection(settings("good")))
    }

    @Test
    fun `failing health check is unreachable`() = runTest {
        serve(token = "good", healthCode = 500)

        val status = client.checkConnection(settings("good"))

        assertTrue(status is ConnectionStatus.Unreachable)
    }

    @Test
    fun `no server is unreachable`() = runTest {
        val url = server.url("/").toString()
        server.close()

        val status = client.checkConnection(settings("good", url))

        assertTrue(status is ConnectionStatus.Unreachable)
    }

    @Test
    fun `missing token or url is not configured`() = runTest {
        assertEquals(ConnectionStatus.NotConfigured, client.checkConnection(settings("")))
        assertEquals(ConnectionStatus.NotConfigured, client.checkConnection(settings("t", url = " ")))
    }

    @Test
    fun `token with non-header characters is invalid, not a crash`() = runTest {
        serve(token = "good")

        for (token in listOf("tok’en", "tok en", "tok\nen", "tok en")) {
            assertEquals(ConnectionStatus.InvalidToken, client.checkConnection(settings(token)))
        }
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `invalid url is unreachable`() = runTest {
        val status = client.checkConnection(settings("t", url = "not a url"))

        assertEquals(ConnectionStatus.Unreachable("Invalid server URL"), status)
    }
}
