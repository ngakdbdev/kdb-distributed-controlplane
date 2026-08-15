"""
crypto.py - encryption-at-rest for secrets this app stores and needs back in
plaintext to actually use (LLM API keys, OAuth client secrets, LDAP bind
passwords). Login passwords are NOT here - those are one-way bcrypt hashes
(security.py) and never need to be reversed.

Previously these lived as plain columns with no encryption at all - the API
layer already masked them in responses (`api_key_set: bool`, never the
value), but the underlying storage itself was plaintext. This closes that
gap without adopting a secrets-manager service: one symmetric key, derived
from a single env var, matching the existing JWT_SECRET/WATCHDOG_SHARED_
SECRET convention (config.py) rather than requiring operators to pre-
generate and store a Fernet-shaped key specifically.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

# Marks a value as encrypted-by-this-module. Lets decrypt_secret() safely
# no-op on rows written before this module existed (plaintext already in
# the DB from earlier in this project's life) instead of needing a one-time
# migration pass - the next save re-encrypts it going forward.
_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    passphrase = os.environ.get("SECRET_ENCRYPTION_KEY", "dev-encryption-key-change-in-deploy")
    # Fernet requires a 32-byte url-safe-base64 key, not an arbitrary
    # string - derive one deterministically via SHA-256 so the operator-
    # facing surface is one plain env var, same shape as every other secret
    # this app already asks for.
    digest = hashlib.sha256(passphrase.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    """Empty string passes through unchanged - there's nothing to protect,
    and an empty value commonly means "not configured" throughout this
    codebase's own conventions (e.g. LLMConfig.api_key, TenantLDAP.
    bind_password)."""
    if not plaintext:
        return plaintext
    return _PREFIX + _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    """Decrypts a value encrypt_secret produced. Returns the input
    UNCHANGED if it doesn't carry this module's prefix (either a pre-
    existing plaintext row, or genuinely empty) - and also unchanged,
    rather than raising, if it carries the prefix but fails to decrypt
    (e.g. SECRET_ENCRYPTION_KEY was rotated without re-encrypting existing
    rows first). The caller then gets a visibly-wrong credential and a
    real, debuggable auth failure downstream instead of this function
    taking down every code path that touches the field."""
    if not value or not value.startswith(_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return value
