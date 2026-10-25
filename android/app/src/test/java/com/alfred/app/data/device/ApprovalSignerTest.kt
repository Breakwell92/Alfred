package com.alfred.app.data.device

import com.alfred.app.testing.FakeDeviceKeys
import java.security.Signature
import java.util.Base64
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ApprovalSignerTest {

    private val keys = FakeDeviceKeys()
    private val signer = ApprovalSigner(keys, nowSeconds = { 1_700_000_000 })

    private fun verifies(proof: ApprovalProof, message: String): Boolean =
        Signature.getInstance("SHA256withECDSA").run {
            initVerify(keys.keyPair.public)
            update(message.toByteArray(Charsets.UTF_8))
            verify(Base64.getDecoder().decode(proof.signature))
        }

    @Test
    fun `the signed message matches the server's format`() {
        // Same string as the server's test_the_signed_message_format_is_fixed (test_devices.py).
        assertEquals(
            "alfred-approval-v1\ndev-1\nconf-1\ntrue\n1700000000",
            approvalMessage("dev-1", "conf-1", approve = true, issuedAt = 1_700_000_000),
        )
    }

    @Test
    fun `signs the answer to one confirmation`() {
        val proof = signer.sign("dev-1", "conf-1", approve = true)

        assertEquals("dev-1", proof.deviceId)
        assertEquals(1_700_000_000, proof.issuedAt)
        assertTrue(verifies(proof, approvalMessage("dev-1", "conf-1", true, 1_700_000_000)))
    }

    @Test
    fun `a signature doesn't cover a different answer`() {
        val proof = signer.sign("dev-1", "conf-1", approve = true)

        assertFalse(verifies(proof, approvalMessage("dev-1", "conf-1", false, 1_700_000_000)))
        assertFalse(verifies(proof, approvalMessage("dev-1", "conf-2", true, 1_700_000_000)))
    }
}
