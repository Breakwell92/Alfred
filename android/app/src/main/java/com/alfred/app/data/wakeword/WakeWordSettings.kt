package com.alfred.app.data.wakeword

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.floatPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/** The wake word settings (Settings → Wake word). */
data class WakeWordSettings(
    val enabled: Boolean = false,
    /** 0 (only clear wake words) to 1 (wakes easily, more false wakes). */
    val sensitivity: Float = DEFAULT_SENSITIVITY,
    val chime: Boolean = true,
    val model: StockWakeWord = StockWakeWord.HeyJarvis,
    /** Stop listening while Do Not Disturb (or bedtime mode) is on. */
    val pauseInDoNotDisturb: Boolean = true,
) {
    /** The classifier score that counts as a wake: 0.8 at sensitivity 0 down to 0.2 at 1. */
    val threshold: Float
        get() = MAX_THRESHOLD - (MAX_THRESHOLD - MIN_THRESHOLD) * sensitivity.coerceIn(0f, 1f)

    companion object {
        /** Gives openWakeWord's default threshold of 0.5. */
        const val DEFAULT_SENSITIVITY = 0.5f
        private const val MAX_THRESHOLD = 0.8f
        private const val MIN_THRESHOLD = 0.2f
    }
}

interface WakeWordSettingsRepository {
    val settings: Flow<WakeWordSettings>

    suspend fun update(transform: (WakeWordSettings) -> WakeWordSettings)
}

/** Stored next to the app settings, in the same DataStore, under its own keys. */
class DataStoreWakeWordSettingsRepository(
    private val dataStore: DataStore<Preferences>,
) : WakeWordSettingsRepository {

    override val settings: Flow<WakeWordSettings> = dataStore.data.map(::read)

    override suspend fun update(transform: (WakeWordSettings) -> WakeWordSettings) {
        dataStore.edit { prefs ->
            val next = transform(read(prefs))
            prefs[ENABLED] = next.enabled
            prefs[SENSITIVITY] = next.sensitivity.coerceIn(0f, 1f)
            prefs[CHIME] = next.chime
            prefs[MODEL] = next.model.name
            prefs[PAUSE_IN_DND] = next.pauseInDoNotDisturb
        }
    }

    private fun read(prefs: Preferences): WakeWordSettings {
        val defaults = WakeWordSettings()
        return WakeWordSettings(
            enabled = prefs[ENABLED] ?: defaults.enabled,
            sensitivity = prefs[SENSITIVITY] ?: defaults.sensitivity,
            chime = prefs[CHIME] ?: defaults.chime,
            model = prefs[MODEL]?.let { name -> StockWakeWord.entries.firstOrNull { it.name == name } }
                ?: defaults.model,
            pauseInDoNotDisturb = prefs[PAUSE_IN_DND] ?: defaults.pauseInDoNotDisturb,
        )
    }

    private companion object {
        val ENABLED = booleanPreferencesKey("wake_word_enabled")
        val SENSITIVITY = floatPreferencesKey("wake_word_sensitivity")
        val CHIME = booleanPreferencesKey("wake_word_chime")
        val MODEL = stringPreferencesKey("wake_word_model")
        val PAUSE_IN_DND = booleanPreferencesKey("wake_word_pause_in_dnd")
    }
}
