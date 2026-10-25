package com.alfred.app.data.voice

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import java.util.UUID
import kotlinx.coroutines.flow.first

/**
 * This install's `device_id` for the WebSocket `hello`: a random UUID made once and kept. The
 * server keys the device's conversation on it, so it must survive restarts. (Not the id the
 * server gives the app when it's paired for approvals; that one lives in `AppSettings`.)
 */
fun interface DeviceIdRepository {
    suspend fun deviceId(): String
}

class DataStoreDeviceIdRepository(
    private val dataStore: DataStore<Preferences>,
    private val newId: () -> String = { UUID.randomUUID().toString() },
) : DeviceIdRepository {

    override suspend fun deviceId(): String {
        dataStore.data.first()[DEVICE_ID]?.let { return it }
        var id = ""
        // Inside edit, so two first calls racing still agree on one id.
        dataStore.edit { prefs -> id = prefs[DEVICE_ID] ?: newId().also { prefs[DEVICE_ID] = it } }
        return id
    }

    private companion object {
        val DEVICE_ID = stringPreferencesKey("device_id")
    }
}
