package com.alfred.app.data.server

import com.alfred.app.data.device.ApprovalProof
import com.alfred.app.data.settings.AppSettings
import java.util.Base64
import kotlinx.coroutines.test.runTest
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import mockwebserver3.RecordedRequest
import okhttp3.OkHttpClient
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** `POST /devices/pair` and `POST /confirmations/{id}`. */
class OkHttpServerClientApprovalsTest {

    private val server = MockWebServer()
    private val client = OkHttpServerClient(OkHttpClient())

    @Before
    fun setUp() = server.start()

    @After
    fun tearDown() = server.close()

    private fun settings(token: String = "good") =
        AppSettings(serverUrl = server.url("/").toString(), apiToken = token)

    private fun RecordedRequest.json() = JSONObject(body!!.utf8())

    @Test
    fun `pairing posts the code and key with the token`() = runTest {
        server.enqueue(MockResponse(code = 200, body = """{"device_id":"d1","name":"pixel"}"""))

        val result = client.pairDevice(settings(), "ABCD-EFGH", byteArrayOf(1, 2, 3))

        assertEquals(PairingResult.Paired("d1", "pixel"), result)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/devices/pair", request.url.encodedPath)
        assertEquals("Bearer good", request.headers["Authorization"])
        val body = request.json()
        assertEquals("ABCD-EFGH", body.getString("code"))
        assertEquals(Base64.getEncoder().encodeToString(byteArrayOf(1, 2, 3)), body.getString("public_key"))
    }

    @Test
    fun `a refused code is invalid`() = runTest {
        server.enqueue(MockResponse(code = 403, body = """{"detail":"wrong"}"""))

        assertEquals(PairingResult.InvalidCode, client.pairDevice(settings(), "x", byteArrayOf(1)))
    }

    @Test
    fun `other pairing answers fail with the server's reason`() = runTest {
        server.enqueue(MockResponse(code = 503, body = """{"detail":"Pairing is unavailable"}"""))

        assertEquals(
            PairingResult.Failed("Pairing is unavailable (503)"),
            client.pairDevice(settings(), "x", byteArrayOf(1)),
        )
    }

    @Test
    fun `pairing without settings sends nothing`() = runTest {
        val result = client.pairDevice(settings(token = ""), "x", byteArrayOf(1))

        assertTrue(result is PairingResult.Failed)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `pairing with no server is a failure, not a crash`() = runTest {
        val unreachable = settings()
        server.close()

        assertTrue(client.pairDevice(unreachable, "x", byteArrayOf(1)) is PairingResult.Failed)
    }

    @Test
    fun `an approval carries the signed proof`() = runTest {
        server.enqueue(MockResponse(code = 200, body = """{"message":"Approved.","reply":"Unlocked."}"""))
        val proof = ApprovalProof("d1", 1_700_000_000, "c2ln")

        val result = client.answerConfirmation(settings(), "conf-1", approve = true, proof = proof)

        assertEquals(AnswerResult.Answered("Unlocked."), result)
        val request = server.takeRequest()
        assertEquals("/confirmations/conf-1", request.url.encodedPath)
        val body = request.json()
        assertTrue(body.getBoolean("approve"))
        assertEquals("app_ui", body.getString("channel"))
        val sent = body.getJSONObject("proof")
        assertEquals("d1", sent.getString("device_id"))
        assertEquals(1_700_000_000, sent.getLong("issued_at"))
        assertEquals("c2ln", sent.getString("signature"))
    }

    @Test
    fun `a denial needs no proof`() = runTest {
        server.enqueue(MockResponse(code = 200, body = """{"message":"Denied.","reply":null}"""))

        val result = client.answerConfirmation(settings(), "conf-1", approve = false, proof = null)

        assertEquals(AnswerResult.Answered(null), result)
        val body = server.takeRequest().json()
        assertFalse(body.getBoolean("approve"))
        assertFalse(body.has("proof"))
    }

    @Test
    fun `forbidden and gone answers keep the server's message`() = runTest {
        server.enqueue(MockResponse(code = 403, body = """{"detail":"Not signed."}"""))
        server.enqueue(MockResponse(code = 409, body = """{"detail":"This confirmation has expired"}"""))
        server.enqueue(MockResponse(code = 502, body = """{"detail":"The agent run failed"}"""))

        assertEquals(
            AnswerResult.Forbidden("Not signed."),
            client.answerConfirmation(settings(), "c", true, null),
        )
        assertEquals(
            AnswerResult.Gone("This confirmation has expired"),
            client.answerConfirmation(settings(), "c", true, null),
        )
        assertEquals(
            AnswerResult.Failed("The agent run failed (502)"),
            client.answerConfirmation(settings(), "c", true, null),
        )
    }
}
