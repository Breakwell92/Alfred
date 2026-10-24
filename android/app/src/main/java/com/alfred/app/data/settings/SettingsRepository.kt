package com.alfred.app.data.settings

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/** Where the server is and the shared bearer token (`ALFRED_API_TOKEN` on the server). */
data class AppSettings(
    val serverUrl: String,
    val apiToken: String,
)

/**
 * A token can be sent in an HTTP header only if it is visible ASCII (0x21-0x7E). Pasted or
 * autocorrected text can bring in smart quotes or non-breaking spaces, which OkHttp rejects.
 */
fun isValidApiToken(token: String): Boolean =
    token.isNotEmpty() && token.all { it.code in 0x21..0x7E }

interface SettingsRepository {
    val settings: Flow<AppSettings>

    suspend fun save(settings: AppSettings)
}

val Context.settingsDataStore: DataStore<Preferences> by preferencesDataStore(name = "settings")

/**
 * Settings in Preferences DataStore (app-private storage). The token is stored unencrypted:
 * fine for a personal, sideloaded app; see docs/decisions/0002-android-client-stack.md.
 */
class DataStoreSettingsRepository(
    private val dataStore: DataStore<Preferences>,
    private val defaultServerUrl: String,
) : SettingsRepository {

    override val settings: Flow<AppSettings> = dataStore.data.map { prefs ->
        AppSettings(
            serverUrl = prefs[SERVER_URL] ?: defaultServerUrl,
            apiToken = prefs[API_TOKEN].orEmpty(),
        )
    }

    override suspend fun save(settings: AppSettings) {
        dataStore.edit { prefs ->
            prefs[SERVER_URL] = settings.serverUrl
            prefs[API_TOKEN] = settings.apiToken
        }
    }

    private companion object {
        val SERVER_URL = stringPreferencesKey("server_url")
        val API_TOKEN = stringPreferencesKey("api_token")
    }
}
