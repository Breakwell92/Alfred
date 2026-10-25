package com.alfred.app.testing

import com.alfred.app.data.device.ApprovalProof
import com.alfred.app.data.device.DeviceKeys
import com.alfred.app.data.server.AnswerResult
import com.alfred.app.data.server.ConnectionStatus
import com.alfred.app.data.server.PairingResult
import com.alfred.app.data.server.ServerClient
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import com.alfred.app.data.settings.AppSettings
import com.alfred.app.data.settings.SettingsRepository
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.test.TestDispatcher
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.rules.TestWatcher
import org.junit.runner.Description

class FakeSettingsRepository(initial: AppSettings) : SettingsRepository {
    private val state = MutableStateFlow(initial)
    override val settings: StateFlow<AppSettings> = state

    /** Like the DataStore repository: saving the URL and token keeps the pairing. */
    override suspend fun save(settings: AppSettings) {
        state.value = settings.copy(
            pairedDeviceId = state.value.pairedDeviceId,
            pairedDeviceName = state.value.pairedDeviceName,
        )
    }

    override suspend fun savePairing(deviceId: String, name: String) {
        state.value = state.value.copy(pairedDeviceId = deviceId, pairedDeviceName = name)
    }
}

/** A plain JVM EC P-256 key standing in for the Android Keystore one. */
class FakeDeviceKeys : DeviceKeys {
    val keyPair: KeyPair = KeyPairGenerator.getInstance("EC").run {
        initialize(ECGenParameterSpec("secp256r1"))
        generateKeyPair()
    }
    var error: Exception? = null

    override fun publicKey(): ByteArray {
        error?.let { throw it }
        return keyPair.public.encoded
    }

    override fun sign(data: ByteArray): ByteArray = Signature.getInstance("SHA256withECDSA").run {
        initSign(keyPair.private)
        update(data)
        sign()
    }
}

/** Answers with [status] (or throws [error]); records every settings value it was asked to check. */
class FakeServerClient(var status: ConnectionStatus = ConnectionStatus.Connected) : ServerClient {
    val checked = mutableListOf<AppSettings>()
    var error: Exception? = null
    var pairing: PairingResult = PairingResult.Paired("device-1", "pixel")
    val paired = mutableListOf<Triple<AppSettings, String, ByteArray>>()

    override suspend fun pairDevice(
        settings: AppSettings,
        code: String,
        publicKey: ByteArray,
    ): PairingResult {
        paired += Triple(settings, code, publicKey)
        return pairing
    }

    override suspend fun answerConfirmation(
        settings: AppSettings,
        confirmationId: String,
        approve: Boolean,
        proof: ApprovalProof?,
    ): AnswerResult = AnswerResult.Answered(null)

    override suspend fun checkConnection(settings: AppSettings): ConnectionStatus {
        checked += settings
        error?.let { throw it }
        return status
    }
}

/** Points `Dispatchers.Main` (used by `viewModelScope`) at a test dispatcher. */
@OptIn(ExperimentalCoroutinesApi::class)
class MainDispatcherRule(val dispatcher: TestDispatcher = UnconfinedTestDispatcher()) : TestWatcher() {
    override fun starting(description: Description) = Dispatchers.setMain(dispatcher)

    override fun finished(description: Description) = Dispatchers.resetMain()
}
