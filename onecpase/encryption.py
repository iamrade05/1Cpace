"""
Field-level encryption for 1Cpace PII.

Strategy
--------
- Fernet (AES-128-CBC + HMAC-SHA256) for all sensitive fields.
- HMAC-SHA256 hash stored separately for searchable fields (id_number).
- Encrypted values are prefixed with ``enc:``.

Security contract
-----------------
- A valid ENCRYPTION_KEY is mandatory for encryption/decryption of encrypted data.
- New sensitive data is never silently stored in plaintext because encryption is
  unavailable.
- Decryption never returns ciphertext after a failed decrypt.
- Existing unencrypted legacy values remain readable so a controlled migration
  can identify and re-encrypt them. New writes must still pass through encrypt().
"""

import hashlib
import hmac
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_PREFIX = "enc:"
_fernet: Fernet | None = None
_fernet_key: str | None = None


class EncryptionConfigurationError(RuntimeError):
    """Raised when encryption is required but ENCRYPTION_KEY is unavailable."""


def _get_fernet() -> Fernet:
    """Return the configured Fernet instance or fail closed.

    The key is deliberately not optional. Callers that need to read an
    existing legacy plaintext value should check the ``enc:`` prefix first;
    callers handling encrypted data must never silently downgrade to raw data.
    """
    global _fernet, _fernet_key
    key = os.environ.get("ENCRYPTION_KEY", "").strip()
    if _fernet is not None and _fernet_key == key:
        return _fernet

    if not key:
        raise EncryptionConfigurationError(
            "ENCRYPTION_KEY is required for field encryption/decryption."
        )

    try:
        _fernet = Fernet(key.encode())
        _fernet_key = key
    except Exception as exc:
        raise EncryptionConfigurationError(
            "ENCRYPTION_KEY is invalid; expected a valid Fernet key."
        ) from exc

    return _fernet


def encryption_enabled() -> bool:
    """Return whether a valid encryption key is configured.

    Unlike the data-path helpers, this predicate does not raise. It is useful
    for startup/diagnostic checks and for callers that need to distinguish
    legacy plaintext rows from encrypted rows.
    """
    try:
        _get_fernet()
        return True
    except EncryptionConfigurationError:
        return False


# ── Core helpers ──────────────────────────────────────────────────────────────

def encrypt(plaintext: str | None) -> str | None:
    """Encrypt *plaintext* and return a prefixed ciphertext string.

    Empty values remain empty. Non-empty values require a valid encryption
    key. Existing ``enc:`` values are returned unchanged.
    """
    if not plaintext:
        return plaintext
    if plaintext.startswith(_PREFIX):
        return plaintext
    f = _get_fernet()
    token = f.encrypt(plaintext.encode()).decode()
    return f"{_PREFIX}{token}"


def decrypt(ciphertext: str | None) -> str | None:
    """Decrypt an encrypted value.

    Values without the ``enc:`` prefix are treated as legacy plaintext so that
    existing data can be migrated safely. Values that are marked encrypted
    require a valid key and a successful decrypt. Ciphertext is NEVER returned
    after a failed decrypt.
    """
    if not ciphertext:
        return ciphertext
    if not ciphertext.startswith(_PREFIX):
        return ciphertext  # legacy plaintext; migration must handle it

    f = _get_fernet()
    try:
        return f.decrypt(ciphertext[len(_PREFIX):].encode()).decode()
    except InvalidToken as exc:
        logger.error(
            "decrypt(): ciphertext could not be authenticated with the configured key"
        )
        raise EncryptionConfigurationError(
            "Encrypted value could not be decrypted; key may be wrong or data corrupted."
        ) from exc


def hash_for_lookup(plaintext: str | None) -> str | None:
    """Return deterministic HMAC-SHA256 for exact-match database lookups.

    A valid encryption key is required because the same secret protects the
    searchable identifier hash.
    """
    if not plaintext:
        return None
    key = os.environ.get("ENCRYPTION_KEY", "").strip()
    if not key:
        raise EncryptionConfigurationError(
            "ENCRYPTION_KEY is required for searchable PII hashes."
        )
    # Validate the key format as well as its presence.
    _get_fernet()
    return hmac.new(key.encode(), plaintext.encode(), hashlib.sha256).hexdigest()


# ── Member field helpers ──────────────────────────────────────────────────────

_MEMBER_ENCRYPT_FIELDS = ("account_number", "branch_code", "payer_id_number")


def encrypt_member(data: dict) -> dict:
    """Return a copy of *data* with PII fields encrypted in-place.
    Also computes id_number_hash when id_number is present.
    """
    out = dict(data)
    if "id_number" in out and out["id_number"]:
        out["id_number_hash"] = hash_for_lookup(out["id_number"])
        out["id_number"] = encrypt(out["id_number"])
    for field in _MEMBER_ENCRYPT_FIELDS:
        if field in out and out[field]:
            out[field] = encrypt(out[field])
    return out


def decrypt_member(row) -> dict:
    """Return a plain dict copy of a DB row with PII fields decrypted."""
    d = dict(row)
    if "id_number" in d:
        d["id_number"] = decrypt(d["id_number"])
    for field in _MEMBER_ENCRYPT_FIELDS:
        if field in d:
            d[field] = decrypt(d[field])
    return d


# ── DebiCheck mandate field helpers ──────────────────────────────────────────

_MANDATE_ENCRYPT_FIELDS = ("account_number", "branch_code", "id_number")


def encrypt_mandate(data: dict) -> dict:
    """Return a copy of a DebiCheck mandate with PII encrypted."""
    out = dict(data)
    for field in _MANDATE_ENCRYPT_FIELDS:
        if field in out and out[field]:
            out[field] = encrypt(out[field])
    return out


def decrypt_mandate(row) -> dict:
    """Return a plain dict copy of a mandate row with PII decrypted."""
    d = dict(row)
    for field in _MANDATE_ENCRYPT_FIELDS:
        if field in d:
            d[field] = decrypt(d[field])
    return d
