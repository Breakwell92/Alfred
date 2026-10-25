package com.alfred.app.data.voice

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The JSON shapes come from docs/protocol.md and the server's `gateway/protocol.py`. */
class ProtocolTest {

    private fun json(message: ClientMessage) = JSONObject(message.encode())

    private fun assertJson(expected: String, message: ClientMessage) =
        assertEquals(JSONObject(expected).toMap(), json(message).toMap())

    @Test
    fun `hello carries protocol, device and app details`() {
        assertJson(
            """{"type":"hello","protocol":1,"device_id":"dev-1","device_type":"phone",
               "app_version":"0.1.0","capabilities":["audio_pcm16"]}""",
            ClientMessage.Hello("dev-1", appVersion = "0.1.0", capabilities = listOf("audio_pcm16")),
        )
    }

    @Test
    fun `hello leaves out optional fields that aren't set`() {
        assertJson(
            """{"type":"hello","protocol":1,"device_id":"dev-1","device_type":"phone"}""",
            ClientMessage.Hello("dev-1"),
        )
        assertJson(
            """{"type":"hello","protocol":1,"device_id":"d","device_type":"phone",
               "token":"secret","session_id":"s-1"}""",
            ClientMessage.Hello("d", token = "secret", sessionId = "s-1"),
        )
    }

    @Test
    fun `hello carries the wake word service's health in seconds`() {
        assertJson(
            """{"type":"hello","protocol":1,"device_id":"d","device_type":"phone",
               "health":{"listening_since":1700000000.5,"restarts":2,"reconnects":5,
                         "last_restart_at":1700000100,"last_gap_s":720}}""",
            ClientMessage.Hello(
                "d",
                health = DeviceHealth(1_700_000_000_500, 2, 5, 1_700_000_100_000, 720_000),
            ),
        )
        assertJson(
            """{"type":"hello","protocol":1,"device_id":"d","device_type":"phone",
               "health":{"restarts":0,"reconnects":0}}""",
            ClientMessage.Hello("d", health = DeviceHealth()),
        )
    }

    @Test
    fun `hello never prints its token`() {
        assertFalse(ClientMessage.Hello("d", token = "secret").toString().contains("secret"))
    }

    @Test
    fun `turn messages`() {
        assertJson("""{"type":"turn.start","turn_id":"t1","sample_rate":16000}""", ClientMessage.TurnStart("t1"))
        assertJson(
            """{"type":"turn.start","turn_id":"t1","sample_rate":16000,"persona_id":"p",
               "context":{"active_room":"lounge"}}""",
            ClientMessage.TurnStart("t1", personaId = "p", activeRoom = "lounge"),
        )
        assertJson("""{"type":"turn.end","turn_id":"t1"}""", ClientMessage.TurnEnd("t1"))
        assertJson("""{"type":"turn.cancel","turn_id":"t1"}""", ClientMessage.TurnCancel("t1"))
    }

    @Test
    fun `ping and confirm reply`() {
        assertJson("""{"type":"ping","t":5}""", ClientMessage.Ping(5))
        assertJson("""{"type":"ping"}""", ClientMessage.Ping())
        assertJson(
            """{"type":"confirm.reply","confirmation_id":"c-1","approve":true,"turn_id":"t9"}""",
            ClientMessage.ConfirmReply("c-1", approve = true, turnId = "t9"),
        )
        assertJson(
            """{"type":"confirm.reply","confirmation_id":"c-1","approve":false}""",
            ClientMessage.ConfirmReply("c-1", approve = false),
        )
    }

    @Test
    fun `new turn ids are valid and distinct`() {
        val ids = List(100) { newTurnId() }
        assertTrue(ids.all(::isValidTurnId))
        assertEquals(100, ids.toSet().size)
        assertFalse(isValidTurnId("has space"))
        assertFalse(isValidTurnId("x".repeat(65)))
    }

    @Test
    fun `parses hello ok`() {
        assertEquals(
            ServerMessage.HelloOk("s-1", "0.1.0", 16_000, 24_000, voice = false),
            parseServerMessage(
                """{"type":"hello.ok","protocol":1,"server_version":"0.1.0","session_id":"s-1",
                   "input_sample_rate":16000,"output_sample_rate":24000,"voice":false}""",
            ),
        )
    }

    @Test
    fun `hello ok may say how long an utterance can be`() {
        val hello = parseServerMessage(
            """{"type":"hello.ok","session_id":"s-1","voice":true,"max_audio_s":30.0}""",
        ) as ServerMessage.HelloOk
        assertEquals(30.0, hello.maxAudioSeconds!!, 0.0)
    }

    @Test
    fun `parses partial transcripts`() {
        assertEquals(
            ServerMessage.TranscriptPartial("t1", stable = "Set a", unstable = "timer for"),
            parseServerMessage("""{"type":"transcript.partial","turn_id":"t1","stable":"Set a","unstable":"timer for"}"""),
        )
        assertEquals(
            ServerMessage.TranscriptPartial("t1", stable = "", unstable = "Set"),
            parseServerMessage("""{"type":"transcript.partial","turn_id":"t1","unstable":"Set"}"""),
        )
    }

    @Test
    fun `parses word timings`() {
        assertEquals(
            ServerMessage.ReplyWords(
                "t1",
                listOf(SpokenWord("At", 40, 210), SpokenWord("7:30.", 210, 900, emphasis = true)),
            ),
            parseServerMessage(
                """{"type":"reply.words","turn_id":"t1","words":[
                   {"word":"At","start_ms":40,"end_ms":210},
                   {"word":"7:30.","start_ms":210,"end_ms":900,"emphasis":true}]}""",
            ),
        )
    }

    @Test
    fun `parses turn messages`() {
        assertEquals(ServerMessage.Pong(3.0), parseServerMessage("""{"type":"pong","t":3}"""))
        assertEquals(
            ServerMessage.TranscriptFinal("t1", "What time is it?"),
            parseServerMessage(
                """{"type":"transcript.final","turn_id":"t1","text":"What time is it?","language":"en","audio_s":1.2}""",
            ),
        )
        assertEquals(
            ServerMessage.ReplyTextDelta("t1", "It's "),
            parseServerMessage("""{"type":"reply.text.delta","turn_id":"t1","text":"It's "}"""),
        )
        assertEquals(
            ServerMessage.ReplyAudioStart("t1", 24_000),
            parseServerMessage(
                """{"type":"reply.audio.start","turn_id":"t1","sample_rate":24000,"encoding":"pcm_s16le","channels":1}""",
            ),
        )
        assertEquals(ServerMessage.ReplyAudioEnd("t1"), parseServerMessage("""{"type":"reply.audio.end","turn_id":"t1"}"""))
        assertEquals(ServerMessage.TurnCancelled("t1"), parseServerMessage("""{"type":"turn.cancelled","turn_id":"t1"}"""))
    }

    @Test
    fun `parses confirm request`() {
        val message = parseServerMessage(
            """{"type":"confirm.request","turn_id":"t1","id":"c-1","summary":"Lock the front door",
               "tier":"irreversible","expires_at":"2026-10-25T13:00:00+00:00"}""",
        ) as ServerMessage.ConfirmRequest
        assertEquals("c-1", message.id)
        assertEquals("Lock the front door", message.summary)
        assertTrue(message.irreversible)
        assertEquals("2026-10-25T13:00:00+00:00", message.expiresAt)
    }

    @Test
    fun `parses turn metadata`() {
        val message = parseServerMessage(
            """{"type":"turn.metadata","turn_id":"t1","specialist":"general","alias":"local-main",
               "tier":"local","reason":"simple","budget_code":null,"tools":["get_time"],
               "input_tokens":120,"output_tokens":9,"cost_gbp":null,
               "timings":{"transcript_ms":210.5,"first_audio_ms":840,"stt_ms":null}}""",
        ) as ServerMessage.Metadata
        assertEquals(
            TurnMetadata(
                turnId = "t1",
                specialist = "general",
                alias = "local-main",
                tier = "local",
                reason = "simple",
                tools = listOf("get_time"),
                inputTokens = 120,
                outputTokens = 9,
                costGbp = null,
                timings = mapOf("transcript_ms" to 210.5, "first_audio_ms" to 840.0),
            ),
            message.metadata,
        )
    }

    @Test
    fun `metadata without routing fields`() {
        val message = parseServerMessage(
            """{"type":"turn.metadata","turn_id":"t1","timings":{"total_ms":12}}""",
        ) as ServerMessage.Metadata
        assertNull(message.metadata.specialist)
        assertEquals(emptyList<String>(), message.metadata.tools)
    }

    @Test
    fun `parses errors with and without a turn`() {
        assertEquals(
            ServerMessage.Error("t1", "no_speech", "Nothing recognised"),
            parseServerMessage("""{"type":"error","turn_id":"t1","code":"no_speech","message":"Nothing recognised"}"""),
        )
        assertEquals(
            ServerMessage.Error(null, "bad_message", ""),
            parseServerMessage("""{"type":"error","turn_id":null,"code":"bad_message"}"""),
        )
    }

    @Test
    fun `unknown types are kept as unknown`() {
        assertEquals(ServerMessage.Unknown("push.notification"), parseServerMessage("""{"type":"push.notification"}"""))
    }

    @Test(expected = ProtocolException::class)
    fun `non json is rejected`() {
        parseServerMessage("nope")
    }

    @Test(expected = ProtocolException::class)
    fun `missing required field is rejected`() {
        parseServerMessage("""{"type":"transcript.final","text":"no turn"}""")
    }

    @Test
    fun `parses reply audio frames`() {
        val pcm = byteArrayOf(1, 2, 3, 4)
        val frame = byteArrayOf(0x01, 2) + "t1".toByteArray() + pcm
        val audio = parseAudioFrame(frame)
        assertEquals("t1", audio.turnId)
        assertArrayEquals(pcm, audio.pcm)
    }

    @Test(expected = ProtocolException::class)
    fun `audio frame with the wrong tag is rejected`() {
        parseAudioFrame(byteArrayOf(0x02, 1, 'a'.code.toByte()))
    }

    @Test(expected = ProtocolException::class)
    fun `audio frame shorter than its turn id is rejected`() {
        parseAudioFrame(byteArrayOf(0x01, 10, 'a'.code.toByte()))
    }

    @Test
    fun `web socket url from the server url`() {
        assertEquals("http://glados:8000/ws/device", webSocketUrl("http://glados:8000").toString())
        assertEquals("http://glados:8000/ws/device", webSocketUrl("http://glados:8000/").toString())
        assertEquals("https://alfred.example/ws/device", webSocketUrl(" https://alfred.example ").toString())
        assertNull(webSocketUrl("not a url"))
    }
}

/** Nested JSON as plain maps and lists, so comparisons ignore key order. */
private fun JSONObject.toMap(): Map<String, Any?> = keys().asSequence().associateWith { key ->
    when (val value = get(key)) {
        is JSONObject -> value.toMap()
        is org.json.JSONArray -> (0 until value.length()).map { value.get(it) }
        is Number -> value.toDouble()
        else -> value
    }
}
