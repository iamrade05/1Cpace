import os

import pytest
from cryptography.fernet import Fernet

from onecpase.encryption import (
    EncryptionConfigurationError,
    decrypt,
    encrypt,
    encryption_enabled,
    hash_for_lookup,
)


TEST_KEY = Fernet.generate_key().decode()


def test_encrypt_decrypt_round_trip(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", TEST_KEY)
    value = encrypt("123456789")
    assert value.startswith("enc:")
    assert decrypt(value) == "123456789"


def test_encrypt_fails_closed_without_key(monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    with pytest.raises(EncryptionConfigurationError):
        encrypt("sensitive")


def test_decrypt_fails_closed_without_key(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", TEST_KEY)
    encrypted = encrypt("sensitive")
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    with pytest.raises(EncryptionConfigurationError):
        decrypt(encrypted)


def test_decrypt_does_not_return_ciphertext_on_wrong_key(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", TEST_KEY)
    encrypted = encrypt("sensitive")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(EncryptionConfigurationError):
        decrypt(encrypted)


def test_legacy_plaintext_remains_readable_for_migration(monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    assert decrypt("legacy-plaintext") == "legacy-plaintext"


def test_hash_requires_key(monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    with pytest.raises(EncryptionConfigurationError):
        hash_for_lookup("123456789")


def test_invalid_key_is_not_treated_as_enabled(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "not-a-fernet-key")
    assert encryption_enabled() is False
    with pytest.raises(EncryptionConfigurationError):
        encrypt("sensitive")
