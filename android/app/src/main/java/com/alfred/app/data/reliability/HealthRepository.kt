package com.alfred.app.data.reliability

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.MutablePreferences
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

interface HealthRepository {
    val health: Flow<ServiceHealth>

    suspend fun update(transform: (ServiceHealth) -> ServiceHealth)
}

/** Stored next to the app settings, in the same DataStore, under its own `health_*` keys. */
class DataStoreHealthRepository(private val dataStore: DataStore<Preferences>) : HealthRepository {

    override val health: Flow<ServiceHealth> = dataStore.data.map(::read)

    override suspend fun update(transform: (ServiceHealth) -> ServiceHealth) {
        dataStore.edit { prefs ->
            val next = transform(read(prefs))
            prefs.putOrRemove(LISTENING_SINCE, next.listeningSince)
            prefs.putOrRemove(LAST_ALIVE_AT, next.lastAliveAt)
            prefs[CLEAN_STOP] = next.cleanStop
            prefs[RESTART_COUNT] = next.restartCount
            prefs.putOrRemove(LAST_RESTART_AT, next.lastRestartAt)
            prefs.putOrRemove(LAST_RESTART_REASON, next.lastRestartReason?.wire)
            prefs.putOrRemove(LAST_GAP, next.lastGapMillis)
            prefs[RECONNECT_COUNT] = next.reconnectCount
        }
    }

    private fun read(prefs: Preferences) = ServiceHealth(
        listeningSince = prefs[LISTENING_SINCE],
        lastAliveAt = prefs[LAST_ALIVE_AT],
        cleanStop = prefs[CLEAN_STOP] ?: true,
        restartCount = prefs[RESTART_COUNT] ?: 0,
        lastRestartAt = prefs[LAST_RESTART_AT],
        lastRestartReason = StartReason.fromWire(prefs[LAST_RESTART_REASON]),
        lastGapMillis = prefs[LAST_GAP],
        reconnectCount = prefs[RECONNECT_COUNT] ?: 0,
    )

    private fun <T> MutablePreferences.putOrRemove(key: Preferences.Key<T>, value: T?) {
        if (value == null) remove(key) else this[key] = value
    }

    private companion object {
        val LISTENING_SINCE = longPreferencesKey("health_listening_since")
        val LAST_ALIVE_AT = longPreferencesKey("health_last_alive_at")
        val CLEAN_STOP = booleanPreferencesKey("health_clean_stop")
        val RESTART_COUNT = intPreferencesKey("health_restart_count")
        val LAST_RESTART_AT = longPreferencesKey("health_last_restart_at")
        val LAST_RESTART_REASON = stringPreferencesKey("health_last_restart_reason")
        val LAST_GAP = longPreferencesKey("health_last_gap_ms")
        val RECONNECT_COUNT = intPreferencesKey("health_reconnect_count")
    }
}
