package com.alfred.app.data.device

import java.util.Base64

/** Sent as `proof` with an `app_ui` answer to `POST /confirmations/{id}`. */
data class ApprovalProof(
    val deviceId: String,
    /** Unix seconds when it was signed; the server accepts it for 5 minutes either way. */
    val issuedAt: Long,
    /** Base64 DER ECDSA signature over [approvalMessage]. */
    val signature: String,
)

/**
 * The exact text that's signed. The server builds the same bytes
 * (`alfred_server.devices.service.approval_message`); change both together.
 */
fun approvalMessage(deviceId: String, confirmationId: String, approve: Boolean, issuedAt: Long): String =
    listOf("alfred-approval-v1", deviceId, confirmationId, approve.toString(), issuedAt.toString())
        .joinToString("\n")

/** Signs an answer to one confirmation with the paired device's key. Blocking (Keystore). */
class ApprovalSigner(
    private val keys: DeviceKeys,
    private val nowSeconds: () -> Long = { System.currentTimeMillis() / 1000 },
) {
    fun sign(deviceId: String, confirmationId: String, approve: Boolean): ApprovalProof {
        val issuedAt = nowSeconds()
        val message = approvalMessage(deviceId, confirmationId, approve, issuedAt)
        val signature = keys.sign(message.toByteArray(Charsets.UTF_8))
        return ApprovalProof(deviceId, issuedAt, Base64.getEncoder().encodeToString(signature))
    }
}
