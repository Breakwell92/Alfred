package com.alfred.app.data.device

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/**
 * The key that signs `app_ui` approvals (server ADR 0014). Its public half is registered with
 * the server at pairing; the private half never leaves the device.
 */
interface DeviceKeys {
    /** X.509 SubjectPublicKeyInfo (DER) of the signing key, creating the key if needed. */
    fun publicKey(): ByteArray

    /** DER ECDSA P-256 / SHA-256 signature over [data]. */
    fun sign(data: ByteArray): ByteArray
}

/**
 * An EC P-256 key in the Android Keystore: hardware-backed where the phone has it, and not
 * exportable. Blocking: call off the main thread. No user-authentication requirement yet; a
 * biometric prompt comes with the confirmation screen.
 */
class AndroidKeyStoreDeviceKeys(private val alias: String = ALIAS) : DeviceKeys {

    override fun publicKey(): ByteArray {
        val keyStore = keyStore()
        if (!keyStore.containsAlias(alias)) generate()
        return keyStore.getCertificate(alias).publicKey.encoded
    }

    override fun sign(data: ByteArray): ByteArray {
        val keyStore = keyStore()
        if (!keyStore.containsAlias(alias)) error("No approval key yet: pair the app first")
        val privateKey = keyStore.getKey(alias, null) as PrivateKey
        return Signature.getInstance(SIGNATURE_ALGORITHM).run {
            initSign(privateKey)
            update(data)
            sign()
        }
    }

    private fun keyStore(): KeyStore = KeyStore.getInstance(PROVIDER).apply { load(null) }

    private fun generate() {
        val spec = KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
            .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
            .setDigests(KeyProperties.DIGEST_SHA256)
            .build()
        KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, PROVIDER).run {
            initialize(spec)
            generateKeyPair()
        }
    }

    private companion object {
        const val PROVIDER = "AndroidKeyStore"
        const val ALIAS = "alfred_approval_key"
        const val SIGNATURE_ALGORITHM = "SHA256withECDSA"
    }
}
