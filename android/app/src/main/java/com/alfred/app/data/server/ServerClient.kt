package com.alfred.app.data.server

import com.alfred.app.data.device.ApprovalProof
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.settings.isValidApiToken
import java.io.IOException
import java.util.Base64
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONException
import org.json.JSONObject

/** What the Home screen shows about the link to the server. */
sealed interface ConnectionStatus {
    /** No server URL or token entered yet. */
    data object NotConfigured : ConnectionStatus

    data object Checking : ConnectionStatus

    /** `/health` is up and `/me` accepted the token. */
    data object Connected : ConnectionStatus

    /** The server is up but rejected the token (401). */
    data object Unauthorised : ConnectionStatus

    /** The saved token has characters that can't go in an HTTP header, so it was never sent. */
    data object InvalidToken : ConnectionStatus

    /** No answer, or `/health` not ok: Tailscale off, server down or wrong URL. */
    data class Unreachable(val reason: String) : ConnectionStatus

    /** Any other answer from `/me`, e.g. 503 when the server has no API token set. */
    data class ServerError(val code: Int) : ConnectionStatus
}

/** The answer to `POST /devices/pair`. */
sealed interface PairingResult {
    data class Paired(val deviceId: String, val name: String) : PairingResult

    /** 403: the code is wrong, already used or expired. */
    data object InvalidCode : PairingResult

    /** Anything else: not set up, unreachable, or another status. */
    data class Failed(val reason: String) : PairingResult
}

/** The answer to `POST /confirmations/{id}`. */
sealed interface AnswerResult {
    /** Approved (the run continued; [reply] is its answer) or denied. */
    data class Answered(val reply: String?) : AnswerResult

    /** 403: this channel or proof can't approve it. */
    data class Forbidden(val message: String) : AnswerResult

    /** 404 or 409: unknown, already answered or expired. */
    data class Gone(val message: String) : AnswerResult

    data class Failed(val reason: String) : AnswerResult
}

interface ServerClient {
    suspend fun checkConnection(settings: AppSettings): ConnectionStatus

    /** Registers [publicKey] (X.509 SubjectPublicKeyInfo DER) with a code from the server CLI. */
    suspend fun pairDevice(settings: AppSettings, code: String, publicKey: ByteArray): PairingResult

    /** Answers a confirmation; an `app_ui` approval of an irreversible action needs [proof]. */
    suspend fun answerConfirmation(
        settings: AppSettings,
        confirmationId: String,
        approve: Boolean,
        proof: ApprovalProof?,
    ): AnswerResult
}

/** A server call's status and body. */
private data class Reply(val code: Int, val body: String)

class OkHttpServerClient(
    private val client: OkHttpClient,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : ServerClient {

    override suspend fun checkConnection(settings: AppSettings): ConnectionStatus {
        if (settings.serverUrl.isBlank() || settings.apiToken.isBlank()) {
            return ConnectionStatus.NotConfigured
        }
        val token = settings.apiToken.trim()
        if (!isValidApiToken(token)) return ConnectionStatus.InvalidToken
        val base = settings.serverUrl.trim().toHttpUrlOrNull()
            ?: return ConnectionStatus.Unreachable("Invalid server URL")

        return withContext(ioDispatcher) {
            try {
                val healthCode = get(base.withPath("health"), token = null)
                if (healthCode != 200) {
                    ConnectionStatus.Unreachable("Health check returned $healthCode")
                } else {
                    when (val meCode = get(base.withPath("me"), token)) {
                        200 -> ConnectionStatus.Connected
                        401 -> ConnectionStatus.Unauthorised
                        else -> ConnectionStatus.ServerError(meCode)
                    }
                }
            } catch (e: IOException) {
                ConnectionStatus.Unreachable(e.message ?: e.javaClass.simpleName)
            }
        }
    }

    override suspend fun pairDevice(
        settings: AppSettings,
        code: String,
        publicKey: ByteArray,
    ): PairingResult {
        val body = JSONObject()
            .put("code", code)
            .put("public_key", Base64.getEncoder().encodeToString(publicKey))
        return when (val outcome = post(settings, listOf("devices", "pair"), body)) {
            is Outcome.NotSent -> PairingResult.Failed(outcome.reason)
            is Outcome.Sent -> when (outcome.reply.code) {
                200 -> try {
                    val json = JSONObject(outcome.reply.body)
                    PairingResult.Paired(json.getString("device_id"), json.getString("name"))
                } catch (e: JSONException) {
                    PairingResult.Failed("Unexpected answer from the server")
                }
                403 -> PairingResult.InvalidCode
                else -> PairingResult.Failed(describe(outcome.reply))
            }
        }
    }

    override suspend fun answerConfirmation(
        settings: AppSettings,
        confirmationId: String,
        approve: Boolean,
        proof: ApprovalProof?,
    ): AnswerResult {
        val body = JSONObject().put("approve", approve).put("channel", "app_ui")
        if (proof != null) {
            body.put(
                "proof",
                JSONObject()
                    .put("device_id", proof.deviceId)
                    .put("issued_at", proof.issuedAt)
                    .put("signature", proof.signature),
            )
        }
        return when (val outcome = post(settings, listOf("confirmations", confirmationId), body)) {
            is Outcome.NotSent -> AnswerResult.Failed(outcome.reason)
            is Outcome.Sent -> when (outcome.reply.code) {
                200 -> AnswerResult.Answered(stringField(outcome.reply, "reply"))
                403 -> AnswerResult.Forbidden(detail(outcome.reply) ?: "Not allowed")
                404, 409 -> AnswerResult.Gone(detail(outcome.reply) ?: "No longer pending")
                else -> AnswerResult.Failed(describe(outcome.reply))
            }
        }
    }

    private sealed interface Outcome {
        data class Sent(val reply: Reply) : Outcome

        data class NotSent(val reason: String) : Outcome
    }

    /** POSTs [json] to [path] with the bearer token, after the same checks as [checkConnection]. */
    private suspend fun post(settings: AppSettings, path: List<String>, json: JSONObject): Outcome {
        if (settings.serverUrl.isBlank() || settings.apiToken.isBlank()) {
            return Outcome.NotSent("Save the server URL and API token first")
        }
        val token = settings.apiToken.trim()
        if (!isValidApiToken(token)) return Outcome.NotSent("The saved API token is invalid")
        val base = settings.serverUrl.trim().toHttpUrlOrNull()
            ?: return Outcome.NotSent("Invalid server URL")
        val url = path.fold(base) { url, segment -> url.withPath(segment) }
        val request = Request.Builder()
            .url(url)
            .header("Authorization", "Bearer $token")
            .post(json.toString().toRequestBody(JSON))
            .build()
        return withContext(ioDispatcher) {
            try {
                client.newCall(request).execute().use { response ->
                    Outcome.Sent(Reply(response.code, response.body.string()))
                }
            } catch (e: IOException) {
                Outcome.NotSent("Server unreachable: ${e.message ?: e.javaClass.simpleName}")
            }
        }
    }

    /**
     * A string field of a JSON body, or null if it's missing, JSON null or the body isn't JSON.
     * (Android's `optString` turns JSON null into "null".)
     */
    private fun stringField(reply: Reply, name: String): String? = try {
        val json = JSONObject(reply.body)
        if (json.isNull(name)) null else json.getString(name).takeIf { it.isNotEmpty() }
    } catch (e: JSONException) {
        null
    }

    /** FastAPI's `{"detail": "..."}`, if the body has one. */
    private fun detail(reply: Reply): String? = stringField(reply, "detail")

    private fun describe(reply: Reply): String =
        detail(reply)?.let { "$it (${reply.code})" } ?: "Server answered ${reply.code}"

    private fun get(url: HttpUrl, token: String?): Int {
        val request = Request.Builder().url(url).apply {
            if (token != null) header("Authorization", "Bearer $token")
        }.build()
        return client.newCall(request).execute().use { it.code }
    }

    private companion object {
        val JSON = "application/json".toMediaType()
    }

    /** `http://host:8000` or `http://host:8000/` plus "me" gives `http://host:8000/me`. */
    private fun HttpUrl.withPath(segment: String): HttpUrl {
        val builder = newBuilder()
        if (pathSegments.lastOrNull() == "") builder.removePathSegment(pathSize - 1)
        return builder.addPathSegment(segment).build()
    }
}
