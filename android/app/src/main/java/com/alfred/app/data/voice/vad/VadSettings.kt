package com.alfred.app.data.voice.vad

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.floatPreferencesKey
import androidx.datastore.preferences.core.intPreferencesKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/** End-of-speech tuning (Settings → Developer, ADR 0023). */
data class VadSettings(
    /** Off: the energy endpointer instead, to compare. */
    val useSilero: Boolean = true,
    val config: VadConfig = VadConfig(),
) {
    /** Each value kept in the range the developer screen offers. */
    fun clamped(): VadSettings = copy(
        config = VadConfig(
            threshold = config.threshold.coerceIn(THRESHOLD_RANGE.start, THRESHOLD_RANGE.endInclusive),
            endSilenceMillis = config.endSilenceMillis.coerceIn(END_SILENCE_RANGE),
            noSpeechMillis = config.noSpeechMillis.coerceIn(NO_SPEECH_RANGE),
            maxUtteranceMillis = config.maxUtteranceMillis.coerceIn(MAX_UTTERANCE_RANGE),
            minSpeechMillis = config.minSpeechMillis.coerceIn(MIN_SPEECH_RANGE),
        ),
    )

    companion object {
        val THRESHOLD_RANGE = 0.2f..0.9f
        val END_SILENCE_RANGE = 300..2_000
        val NO_SPEECH_RANGE = 2_000..10_000
        val MAX_UTTERANCE_RANGE = 5_000..60_000
        val MIN_SPEECH_RANGE = 64..1_000
    }
}

interface VadSettingsRepository {
    val settings: Flow<VadSettings>

    suspend fun update(transform: (VadSettings) -> VadSettings)
}

/** Stored next to the app settings, in the same DataStore, under its own keys. */
class DataStoreVadSettingsRepository(
    private val dataStore: DataStore<Preferences>,
) : VadSettingsRepository {

    override val settings: Flow<VadSettings> = dataStore.data.map(::read)

    override suspend fun update(transform: (VadSettings) -> VadSettings) {
        dataStore.edit { prefs ->
            val next = transform(read(prefs)).clamped()
            prefs[USE_SILERO] = next.useSilero
            prefs[THRESHOLD] = next.config.threshold
            prefs[END_SILENCE] = next.config.endSilenceMillis
            prefs[NO_SPEECH] = next.config.noSpeechMillis
            prefs[MAX_UTTERANCE] = next.config.maxUtteranceMillis
            prefs[MIN_SPEECH] = next.config.minSpeechMillis
        }
    }

    private fun read(prefs: Preferences): VadSettings {
        val defaults = VadConfig()
        return VadSettings(
            useSilero = prefs[USE_SILERO] ?: true,
            config = VadConfig(
                threshold = prefs[THRESHOLD] ?: defaults.threshold,
                endSilenceMillis = prefs[END_SILENCE] ?: defaults.endSilenceMillis,
                noSpeechMillis = prefs[NO_SPEECH] ?: defaults.noSpeechMillis,
                maxUtteranceMillis = prefs[MAX_UTTERANCE] ?: defaults.maxUtteranceMillis,
                minSpeechMillis = prefs[MIN_SPEECH] ?: defaults.minSpeechMillis,
            ),
        ).clamped()
    }

    private companion object {
        val USE_SILERO = booleanPreferencesKey("vad_use_silero")
        val THRESHOLD = floatPreferencesKey("vad_threshold")
        val END_SILENCE = intPreferencesKey("vad_end_silence_ms")
        val NO_SPEECH = intPreferencesKey("vad_no_speech_ms")
        val MAX_UTTERANCE = intPreferencesKey("vad_max_utterance_ms")
        val MIN_SPEECH = intPreferencesKey("vad_min_speech_ms")
    }
}
