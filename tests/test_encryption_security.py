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


def test_app_key_controls_encryption_and_lookup_hash(app, monkeypatch):
    import hashlib
    import hmac

    # A shell key must not override this app's configured database key.
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    with app.app_context():
        key = app.config["ENCRYPTION_KEY"]
        encrypted = encrypt("app-sensitive")
        assert Fernet(key.encode()).decrypt(encrypted[4:].encode()) == b"app-sensitive"
        assert decrypt(encrypted) == "app-sensitive"
        assert hash_for_lookup("app-sensitive") == hmac.new(
            key.encode(), b"app-sensitive", hashlib.sha256
        ).hexdigest()


def test_missing_app_key_does_not_fall_back_to_shell(app, monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", TEST_KEY)
    app.config["ENCRYPTION_KEY"] = ""
    with app.app_context():
        with pytest.raises(EncryptionConfigurationError):
            encrypt("sensitive")


def test_startup_rejects_invalid_app_key(monkeypatch):
    from onecpase import create_app

    monkeypatch.setenv("ENCRYPTION_KEY", TEST_KEY)
    with pytest.raises(EncryptionConfigurationError):
        create_app({"TESTING": True, "ENCRYPTION_KEY": "invalid"})
