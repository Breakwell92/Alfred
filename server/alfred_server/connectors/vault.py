"""Encryption for connector secrets (API keys, OAuth client secrets and tokens).

Fernet (AES-128-CBC with an HMAC-SHA256 tag) with the key from `ALFRED_VAULT_KEY`. Only
ciphertext is written to Postgres; decrypted values exist only in memory while used.
Generate a key with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class VaultError(RuntimeError):
    """A ciphertext couldn't be decrypted: wrong key, or the data was tampered with."""


class VaultUnavailableError(RuntimeError):
    """`ALFRED_VAULT_KEY` is unset or invalid, so no secret can be stored or read."""


class Vault:
    def __init__(self, key: str | None) -> None:
        if not key:
            self._fernet: Fernet | None = None
            self._problem = "ALFRED_VAULT_KEY is not set"
            return
        try:
            self._fernet = Fernet(key.encode())
        except ValueError:
            # Never echo the key itself.
            self._fernet = None
            self._problem = "ALFRED_VAULT_KEY is not a valid Fernet key"
            return
        self._problem = ""

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def _require(self) -> Fernet:
        if self._fernet is None:
            raise VaultUnavailableError(self._problem)
        return self._fernet

    def encrypt(self, data: dict[str, Any]) -> str:
        """Encrypt a JSON-serialisable dict to an ASCII token."""
        return self._require().encrypt(json.dumps(data).encode()).decode()

    def decrypt(self, token: str) -> dict[str, Any]:
        try:
            plain = self._require().decrypt(token.encode())
        except InvalidToken as exc:
            raise VaultError("can't decrypt connector secret (wrong ALFRED_VAULT_KEY?)") from exc
        value = json.loads(plain)
        if not isinstance(value, dict):
            raise VaultError("connector secret is not an object")
        return value
