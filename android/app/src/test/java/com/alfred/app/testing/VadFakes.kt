package com.alfred.app.testing

import com.alfred.app.data.voice.vad.EndpointLog
import com.alfred.app.data.voice.vad.EndpointRecord
import com.alfred.app.data.voice.vad.VadModel
import com.alfred.app.data.voice.vad.VadSettings
import com.alfred.app.data.voice.vad.VadSettingsRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

class FakeVadSettingsRepository(initial: VadSettings = VadSettings()) : VadSettingsRepository {
    override val settings = MutableStateFlow(initial)

    override suspend fun update(transform: (VadSettings) -> VadSettings) {
        settings.value = transform(settings.value).clamped()
    }
}

class FakeEndpointLog : EndpointLog {
    override val recent = MutableStateFlow<List<EndpointRecord>>(emptyList())
    var loads = 0

    override suspend fun load() {
        loads++
    }

    override suspend fun record(record: EndpointRecord) {
        recent.value = listOf(record) + recent.value
    }
}

/** Calls a chunk speech when its mean absolute sample is above 0.1 (about −20 dBFS). */
class LoudnessVadModel : VadModel {
    override fun probability(input: FloatArray, state: FloatArray): Float {
        var sum = 0.0
        for (i in 64 until input.size) sum += kotlin.math.abs(input[i])
        return if (sum / (input.size - 64) > 0.1) 0.95f else 0.02f
    }

    override fun close() = Unit
}

val StateFlow<List<EndpointRecord>>.latest get() = value.firstOrNull()
