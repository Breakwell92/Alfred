import pytest
from cryptography.fernet import Fernet

from alfred_server.connectors.vault import Vault, VaultError, VaultUnavailableError
from tests.connector_helpers import VAULT_KEY

SECRET = {"api_key": "sk-test-123", "client_secret": "shh", "nested": {"n": 1}}


def test_round_trip() -> None:
    vault = Vault(VAULT_KEY)
    token = vault.encrypt(SECRET)

    assert "sk-test-123" not in token
    assert vault.decrypt(token) == SECRET


def test_same_plaintext_encrypts_differently() -> None:
    vault = Vault(VAULT_KEY)
    assert vault.encrypt(SECRET) != vault.encrypt(SECRET)


def test_wrong_key_fails() -> None:
    token = Vault(VAULT_KEY).encrypt(SECRET)
    with pytest.raises(VaultError):
        Vault(Fernet.generate_key().decode()).decrypt(token)


def test_tampered_ciphertext_fails() -> None:
    token = Vault(VAULT_KEY).encrypt(SECRET)
    tampered = token[:-6] + ("A" if token[-6] != "A" else "B") + token[-5:]
    with pytest.raises(VaultError):
        Vault(VAULT_KEY).decrypt(tampered)


@pytest.mark.parametrize("key", [None, "", "not-a-fernet-key"])
def test_missing_or_invalid_key_fails_closed(key: str | None) -> None:
    vault = Vault(key)

    assert not vault.available
    with pytest.raises(VaultUnavailableError) as exc:
        vault.encrypt(SECRET)
    assert "not-a-fernet-key" not in str(exc.value)
    with pytest.raises(VaultUnavailableError):
        vault.decrypt("anything")
