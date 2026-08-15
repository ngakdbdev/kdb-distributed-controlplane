"""Tests for encryption-at-rest (app/crypto.py) - the LLM API key / OAuth
client secret / LDAP bind password protection added this session."""
from app import crypto


def test_round_trips_a_real_value():
    encrypted = crypto.encrypt_secret("sk-real-secret-value")
    assert encrypted != "sk-real-secret-value"
    assert encrypted.startswith("enc:v1:")
    assert crypto.decrypt_secret(encrypted) == "sk-real-secret-value"


def test_empty_string_passes_through_unchanged():
    assert crypto.encrypt_secret("") == ""
    assert crypto.decrypt_secret("") == ""


def test_pre_existing_plaintext_value_decrypts_as_itself():
    # rows written before this module existed are plain, unprefixed text -
    # decrypt_secret must return them unchanged rather than erroring, so no
    # one-time migration pass is needed; the next save re-encrypts it.
    assert crypto.decrypt_secret("already-plaintext-legacy-value") == "already-plaintext-legacy-value"


def test_corrupted_ciphertext_returns_unchanged_rather_than_raising():
    assert crypto.decrypt_secret("enc:v1:not-actually-valid-fernet-data") == "enc:v1:not-actually-valid-fernet-data"
