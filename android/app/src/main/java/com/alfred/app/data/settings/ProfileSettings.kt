package com.alfred.app.data.settings

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.MutablePreferences
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/**
 * How Home addresses Dan (ADR 0022). Null means "not set": Home then uses the string resource
 * defaults (the design's copy). [personaName] is the last active persona the server reported,
 * kept so the PROTOCOL tile still shows it offline.
 */
data class ProfileSettings(
    /** "GOOD EVENING, {name}". */
    val name: String? = null,
    /** The status line's place label, e.g. "GOTHAM". */
    val location: String? = null,
    val personaName: String? = null,
)

interface ProfileSettingsRepository {
    val settings: Flow<ProfileSettings>

    suspend fun update(transform: (ProfileSettings) -> ProfileSettings)
}

/** Stored next to the app settings, in the same DataStore, under its own keys. */
class DataStoreProfileSettingsRepository(
    private val dataStore: DataStore<Preferences>,
) : ProfileSettingsRepository {

    override val settings: Flow<ProfileSettings> = dataStore.data.map(::read)

    override suspend fun update(transform: (ProfileSettings) -> ProfileSettings) {
        dataStore.edit { prefs ->
            val next = transform(read(prefs))
            prefs.put(NAME, next.name)
            prefs.put(LOCATION, next.location)
            prefs.put(PERSONA_NAME, next.personaName)
        }
    }

    private fun read(prefs: Preferences) = ProfileSettings(
        name = prefs[NAME],
        location = prefs[LOCATION],
        personaName = prefs[PERSONA_NAME],
    )

    /** Blank clears the value, so the default shows again. */
    private fun MutablePreferences.put(
        key: Preferences.Key<String>,
        value: String?,
    ) {
        val trimmed = value?.trim()
        if (trimmed.isNullOrEmpty()) remove(key) else this[key] = trimmed
    }

    private companion object {
        val NAME = stringPreferencesKey("profile_name")
        val LOCATION = stringPreferencesKey("profile_location")
        val PERSONA_NAME = stringPreferencesKey("profile_persona_name")
    }
}
