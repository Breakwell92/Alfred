"""Pairing the Alfred app and checking its signed approvals.

The app keeps an EC P-256 key in the Android Keystore (it can't be exported) and registers the
public half once, with a one-time code that only someone with a shell on the server can mint
(`python -m alfred_server.devices pair`). Every `app_ui` approval is then signed over
`approval_message(...)`, so the shared API token alone can't approve an `irreversible` tool.

Design: docs/decisions/0016-app-approval-signing.md.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel
from sqlalchemy import create_engine

from alfred_server.config import Settings, get_settings
from alfred_server.devices.store import DeviceStore, PairedDevice

# Crockford base32: no I, L, O or U, so a code read off a terminal is hard to mistype.
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8  # 40 bits; single use, short-lived, and pairing needs the API token too.
CODE_TTL = timedelta(minutes=10)
# How far an approval's `issued_at` may be from the server's clock (phone clock skew included).
# A confirmation can only be answered once, so this bounds replay, it doesn't prevent it.
APPROVAL_MAX_AGE = timedelta(minutes=5)
MESSAGE_PREFIX = "alfred-approval-v1"


class PairingError(ValueError):
    """The pairing code is wrong, used or expired, or the public key isn't acceptable."""


class ApprovalProofError(ValueError):
    """An approval's signature doesn't check out against a paired, unrevoked device."""


class ApprovalProof(BaseModel):
    """What the app sends with an `app_ui` approval."""

    device_id: str
    # Unix seconds when the app signed it.
    issued_at: int
    # Base64 DER ECDSA (P-256, SHA-256) signature over `approval_message(...)`.
    signature: str


def approval_message(device_id: str, confirmation_id: str, approve: bool, issued_at: int) -> bytes:
    """The exact bytes the app signs. The Android app builds the same string
    (`ApprovalSigner.approvalMessage`); change both together."""
    fields = [MESSAGE_PREFIX, device_id, confirmation_id, "true" if approve else "false"]
    return "\n".join([*fields, str(issued_at)]).encode()


def normalise_code(code: str) -> str:
    """Upper case, separators dropped, and the letters people confuse with digits mapped."""
    cleaned = "".join(ch for ch in code.upper() if ch.isalnum())
    return cleaned.translate(str.maketrans("OIL", "011"))


def _hash_code(code: str) -> str:
    return hashlib.sha256(normalise_code(code).encode()).hexdigest()


def load_public_key(public_key_b64: str) -> ec.EllipticCurvePublicKey:
    """Parse a base64 SubjectPublicKeyInfo. Only EC P-256 keys are accepted."""
    try:
        key = serialization.load_der_public_key(base64.b64decode(public_key_b64, validate=True))
    except (ValueError, binascii.Error, TypeError) as exc:
        raise PairingError("The public key isn't a valid DER SubjectPublicKeyInfo") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise PairingError("The public key must be an EC P-256 key")
    return key


class DeviceService:
    def __init__(
        self,
        store: DeviceStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.clock = clock

    def create_pairing_code(self, name: str, ttl: timedelta = CODE_TTL) -> str:
        """Mint a one-time code (`ABCD-EFGH`) for pairing a device called `name`."""
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        now = self.clock()
        self.store.add_code(_hash_code(code), name, now, now + ttl)
        return f"{code[:4]}-{code[4:]}"

    def pair(self, code: str, public_key_b64: str) -> PairedDevice:
        """Register a device's public key, using up `code`. Raises `PairingError`."""
        # Check the key first, so a bad key doesn't burn the code.
        load_public_key(public_key_b64)
        now = self.clock()
        name = self.store.claim_code(_hash_code(code), now)
        if name is None:
            raise PairingError("The pairing code is wrong, already used or expired")
        device = PairedDevice(
            id=str(uuid.uuid4()), name=name, public_key=public_key_b64, created_at=now
        )
        self.store.add_device(device)
        return device

    def verify_approval(
        self, proof: ApprovalProof, confirmation_id: str, approve: bool
    ) -> PairedDevice:
        """Check `proof` signs this answer to this confirmation, from a paired device.

        Returns the device. Raises `ApprovalProofError`.
        """
        device = self.store.get(proof.device_id)
        if device is None or device.revoked_at is not None:
            raise ApprovalProofError("Unknown or revoked device")
        now = self.clock()
        # Compared as whole Unix seconds in ints: a datetime or float built from an untrusted
        # int can overflow (e.g. 10**400), which must be a 403, not a 503.
        age = abs(int(now.timestamp()) - proof.issued_at)
        if age > int(APPROVAL_MAX_AGE.total_seconds()):
            raise ApprovalProofError("The approval is too old (or the phone's clock is wrong)")
        try:
            signature = base64.b64decode(proof.signature, validate=True)
            key = load_public_key(device.public_key)
            key.verify(
                signature,
                approval_message(proof.device_id, confirmation_id, approve, proof.issued_at),
                ec.ECDSA(hashes.SHA256()),
            )
        except (InvalidSignature, PairingError, ValueError, binascii.Error) as exc:
            raise ApprovalProofError("The approval's signature is invalid") from exc
        self.store.touch(device.id, now)
        return device

    def devices(self) -> list[PairedDevice]:
        return self.store.all()

    def revoke(self, device_id: str) -> bool:
        return self.store.revoke(device_id, self.clock())


def build_device_service(settings: Settings | None = None) -> DeviceService:
    settings = settings or get_settings()
    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    return DeviceService(DeviceStore(engine))


@lru_cache
def get_device_service() -> DeviceService:
    """The process-wide device service. Use as a FastAPI dependency (overridden in tests)."""
    return build_device_service()
