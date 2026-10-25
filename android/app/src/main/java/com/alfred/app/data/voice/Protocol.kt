package com.alfred.app.data.voice

import java.util.UUID
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

/*
 * Device ↔ server WebSocket protocol, version 1 (docs/protocol.md; server side
 * `alfred_server/voice/gateway/protocol.py`). Text frames are JSON with a `type`; binary
 * frames are audio. Keep this file, the doc and the server in step.
 */

const val PROTOCOL_VERSION = 1

/** Microphone audio sent up: mono signed 16-bit little-endian PCM. */
const val INPUT_SAMPLE_RATE = 16_000

/** Reply audio coming down, same encoding. */
const val OUTPUT_SAMPLE_RATE = 24_000

/** First byte of a server → device binary frame holding reply audio. */
const val AUDIO_FRAME: Byte = 0x01

/** Close codes the server uses (docs/protocol.md, "Close codes"). */
object CloseCode {
    const val NORMAL = 1000
    const val REPLACED = 4000
    const val BAD_HELLO = 4400
    const val UNAUTHORISED = 4401
    const val TIMEOUT = 4408
    const val NO_SERVER_TOKEN = 4503
}

private val TURN_ID = Regex("^[A-Za-z0-9._:-]{1,64}$")

/** A fresh turn id: `t-` and 12 hex characters, well inside the protocol's 64. */
fun newTurnId(): String = "t-" + UUID.randomUUID().toString().replace("-", "").take(12)

fun isValidTurnId(id: String): Boolean = TURN_ID.matches(id)

/** Client → server control messages. */
sealed interface ClientMessage {
    fun toJson(): JSONObject

    fun encode(): String = toJson().toString()

    data class Hello(
        val deviceId: String,
        val appVersion: String? = null,
        val capabilities: List<String> = emptyList(),
        val token: String? = null,
        val sessionId: String? = null,
    ) : ClientMessage {
        override fun toJson(): JSONObject = message("hello")
            .put("protocol", PROTOCOL_VERSION)
            .put("device_id", deviceId)
            .put("device_type", "phone")
            .putOpt("app_version", appVersion)
            .apply { if (capabilities.isNotEmpty()) put("capabilities", JSONArray(capabilities)) }
            .putOpt("token", token)
            .putOpt("session_id", sessionId)

        // Never log the token.
        override fun toString(): String =
            "Hello(deviceId=$deviceId, appVersion=$appVersion, sessionId=$sessionId)"
    }

    data class Ping(val t: Long? = null) : ClientMessage {
        override fun toJson(): JSONObject = message("ping").putOpt("t", t)
    }

    data class TurnStart(
        val turnId: String,
        val sampleRate: Int = INPUT_SAMPLE_RATE,
        val personaId: String? = null,
        val activeRoom: String? = null,
        val location: String? = null,
    ) : ClientMessage {
        override fun toJson(): JSONObject = message("turn.start")
            .put("turn_id", turnId)
            .put("sample_rate", sampleRate)
            .putOpt("persona_id", personaId)
            .apply {
                if (activeRoom != null || location != null) {
                    put(
                        "context",
                        JSONObject().putOpt("active_room", activeRoom).putOpt("location", location),
                    )
                }
            }
    }

    data class TurnEnd(val turnId: String) : ClientMessage {
        override fun toJson(): JSONObject = message("turn.end").put("turn_id", turnId)
    }

    data class TurnCancel(val turnId: String) : ClientMessage {
        override fun toJson(): JSONObject = message("turn.cancel").put("turn_id", turnId)
    }

    /** The app's yes/no for a `confirm.request`; its spoken answer is turn [turnId]. */
    data class ConfirmReply(
        val confirmationId: String,
        val approve: Boolean,
        val turnId: String? = null,
    ) : ClientMessage {
        override fun toJson(): JSONObject = message("confirm.reply")
            .put("confirmation_id", confirmationId)
            .put("approve", approve)
            .putOpt("turn_id", turnId)
    }
}

private fun message(type: String): JSONObject = JSONObject().put("type", type)

/** How a turn ran (`turn.metadata`). Routing fields are null when no agent ran. */
data class TurnMetadata(
    val turnId: String,
    val specialist: String? = null,
    val alias: String? = null,
    /** `local` or `cloud`. */
    val tier: String? = null,
    val reason: String? = null,
    val tools: List<String> = emptyList(),
    val inputTokens: Int? = null,
    val outputTokens: Int? = null,
    val costGbp: Double? = null,
    /** Milliseconds from end of speech: `transcript_ms`, `first_audio_ms`, `total_ms`, … */
    val timings: Map<String, Double> = emptyMap(),
)

/** Server → client control messages. */
sealed interface ServerMessage {
    data class HelloOk(
        val sessionId: String,
        val serverVersion: String?,
        val inputSampleRate: Int,
        val outputSampleRate: Int,
        /** False when the server has STT or TTS off; turns are then refused. */
        val voice: Boolean,
    ) : ServerMessage

    data class Pong(val t: Double?) : ServerMessage

    data class TranscriptFinal(val turnId: String, val text: String) : ServerMessage

    data class ReplyTextDelta(val turnId: String, val text: String) : ServerMessage

    data class ReplyAudioStart(val turnId: String, val sampleRate: Int) : ServerMessage

    data class ConfirmRequest(
        val turnId: String,
        val id: String,
        val summary: String,
        /** `write` (voice or `confirm.reply`) or `irreversible` (signed app approval only). */
        val tier: String,
        /** ISO-8601, as sent; null if missing. */
        val expiresAt: String?,
    ) : ServerMessage {
        val irreversible: Boolean get() = tier == "irreversible"
    }

    data class Metadata(val metadata: TurnMetadata) : ServerMessage

    /** Last message of a successful turn. */
    data class ReplyAudioEnd(val turnId: String) : ServerMessage

    data class TurnCancelled(val turnId: String) : ServerMessage

    /** With a [turnId] it's the last message of that turn. */
    data class Error(val turnId: String?, val code: String, val message: String) : ServerMessage

    /** A type this app doesn't know (a newer server). Ignored. */
    data class Unknown(val type: String) : ServerMessage
}

class ProtocolException(message: String, cause: Throwable? = null) : Exception(message, cause)

/** Parses one text frame. Unknown fields are ignored; a missing required field throws. */
fun parseServerMessage(text: String): ServerMessage {
    val json = try {
        JSONObject(text)
    } catch (e: JSONException) {
        throw ProtocolException("Not a JSON object", e)
    }
    return try {
        when (val type = json.getString("type")) {
            "hello.ok" -> ServerMessage.HelloOk(
                sessionId = json.getString("session_id"),
                serverVersion = json.stringOrNull("server_version"),
                inputSampleRate = json.optInt("input_sample_rate", INPUT_SAMPLE_RATE),
                outputSampleRate = json.optInt("output_sample_rate", OUTPUT_SAMPLE_RATE),
                voice = json.optBoolean("voice", true),
            )
            "pong" -> ServerMessage.Pong(json.doubleOrNull("t"))
            "transcript.final" -> ServerMessage.TranscriptFinal(json.turnId(), json.getString("text"))
            "reply.text.delta" -> ServerMessage.ReplyTextDelta(json.turnId(), json.getString("text"))
            "reply.audio.start" -> ServerMessage.ReplyAudioStart(
                json.turnId(),
                json.optInt("sample_rate", OUTPUT_SAMPLE_RATE),
            )
            "confirm.request" -> ServerMessage.ConfirmRequest(
                turnId = json.turnId(),
                id = json.getString("id"),
                summary = json.getString("summary"),
                tier = json.getString("tier"),
                expiresAt = json.stringOrNull("expires_at"),
            )
            "turn.metadata" -> ServerMessage.Metadata(parseMetadata(json))
            "reply.audio.end" -> ServerMessage.ReplyAudioEnd(json.turnId())
            "turn.cancelled" -> ServerMessage.TurnCancelled(json.turnId())
            "error" -> ServerMessage.Error(
                turnId = json.stringOrNull("turn_id"),
                code = json.getString("code"),
                message = json.stringOrNull("message").orEmpty(),
            )
            else -> ServerMessage.Unknown(type)
        }
    } catch (e: JSONException) {
        throw ProtocolException("Bad message: ${e.message}", e)
    }
}

private fun parseMetadata(json: JSONObject): TurnMetadata {
    val tools = json.optJSONArray("tools")?.let { array ->
        (0 until array.length()).mapNotNull { i ->
            // Tool entries may be names or objects with a `name`.
            when (val item = array.opt(i)) {
                is String -> item
                is JSONObject -> item.stringOrNull("name")
                else -> null
            }
        }
    }.orEmpty()
    val timings = json.optJSONObject("timings")?.let { obj ->
        obj.keys().asSequence().mapNotNull { key -> obj.doubleOrNull(key)?.let { key to it } }.toMap()
    }.orEmpty()
    return TurnMetadata(
        turnId = json.turnId(),
        specialist = json.stringOrNull("specialist"),
        alias = json.stringOrNull("alias"),
        tier = json.stringOrNull("tier"),
        reason = json.stringOrNull("reason"),
        tools = tools,
        inputTokens = json.doubleOrNull("input_tokens")?.toInt(),
        outputTokens = json.doubleOrNull("output_tokens")?.toInt(),
        costGbp = json.doubleOrNull("cost_gbp"),
        timings = timings,
    )
}

private fun JSONObject.turnId(): String = getString("turn_id")

/** Android's `optString` turns JSON null into "null"; this doesn't. */
private fun JSONObject.stringOrNull(name: String): String? =
    if (!has(name) || isNull(name)) null else opt(name)?.toString()

private fun JSONObject.doubleOrNull(name: String): Double? =
    if (!has(name) || isNull(name)) null else (opt(name) as? Number)?.toDouble()

/** Reply audio for one turn: 24 kHz PCM16 mono. */
class ReplyAudio(val turnId: String, val pcm: ByteArray)

/** Splits a binary frame: `0x01`, one byte n, n bytes of turn id (ASCII), then PCM. */
fun parseAudioFrame(frame: ByteArray): ReplyAudio {
    if (frame.size < 2 || frame[0] != AUDIO_FRAME) throw ProtocolException("Not a reply-audio frame")
    val idLength = frame[1].toInt() and 0xFF
    val end = 2 + idLength
    if (frame.size < end) throw ProtocolException("Reply-audio frame shorter than its turn id")
    val turnId = String(frame, 2, idLength, Charsets.US_ASCII)
    return ReplyAudio(turnId, frame.copyOfRange(end, frame.size))
}
