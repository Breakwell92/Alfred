"""A stand-in for the Alfred app's Keystore key: pairs with a `DeviceService` and signs approvals
exactly as the app does (`ApprovalSigner` on Android)."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from alfred_server.devices.service import DeviceService, approval_message


class FakeApp:
    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.device_id = ""

    @property
    def public_key(self) -> str:
        der = self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return base64.b64encode(der).decode()

    def pair(self, devices: DeviceService, name: str = "pixel") -> str:
        code = devices.create_pairing_code(name)
        self.device_id = devices.pair(code, self.public_key).id
        return self.device_id

    def proof(
        self,
        confirmation_id: str,
        approve: bool = True,
        *,
        at: datetime,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        """A signed `proof` body. `device_id` overrides the id that's sent (not the one signed)."""
        issued_at = int(at.timestamp())
        message = approval_message(self.device_id, confirmation_id, approve, issued_at)
        signature = self.key.sign(message, ec.ECDSA(hashes.SHA256()))
        return {
            "device_id": device_id or self.device_id,
            "issued_at": issued_at,
            "signature": base64.b64encode(signature).decode(),
        }
