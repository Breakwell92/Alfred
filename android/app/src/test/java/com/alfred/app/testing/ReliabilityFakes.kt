package com.alfred.app.testing

import com.alfred.app.data.reliability.HealthRepository
import com.alfred.app.data.reliability.ReliabilityEvent
import com.alfred.app.data.reliability.ReliabilityLog
import com.alfred.app.data.reliability.ServiceHealth
import kotlinx.coroutines.flow.MutableStateFlow

class FakeHealthRepository(initial: ServiceHealth = ServiceHealth()) : HealthRepository {
    override val health = MutableStateFlow(initial)

    override suspend fun update(transform: (ServiceHealth) -> ServiceHealth) {
        health.value = transform(health.value)
    }
}

class FakeReliabilityLog : ReliabilityLog {
    val events = mutableListOf<ReliabilityEvent>()

    override suspend fun record(event: ReliabilityEvent) {
        events += event
    }
}
