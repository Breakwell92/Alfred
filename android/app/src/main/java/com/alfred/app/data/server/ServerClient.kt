package com.alfred.app.data.server

import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.settings.isValidApiToken
import java.io.IOException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request

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

interface ServerClient {
    suspend fun checkConnection(settings: AppSettings): ConnectionStatus
}

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

    private fun get(url: HttpUrl, token: String?): Int {
        val request = Request.Builder().url(url).apply {
            if (token != null) header("Authorization", "Bearer $token")
        }.build()
        return client.newCall(request).execute().use { it.code }
    }

    /** `http://host:8000` or `http://host:8000/` plus "me" gives `http://host:8000/me`. */
    private fun HttpUrl.withPath(segment: String): HttpUrl {
        val builder = newBuilder()
        if (pathSegments.lastOrNull() == "") builder.removePathSegment(pathSize - 1)
        return builder.addPathSegment(segment).build()
    }
}
