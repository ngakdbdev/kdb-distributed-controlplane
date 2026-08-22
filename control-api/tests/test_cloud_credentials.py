"""Tests for app/cloud_credentials.py - the Fernet encryption wrapper around
raw AWS/Azure/GCP credentials CloudProvisionRun.credentials_encrypted holds."""
import pytest
from cryptography.fernet import Fernet

from app import cloud_credentials
from app.config import settings


@pytest.fixture(autouse=True)
def _real_key(monkeypatch):
    monkeypatch.setattr(settings, "cloud_credentials_encryption_key", Fernet.generate_key().decode())


def test_encrypt_then_decrypt_roundtrips():
    creds = {"access_key_id": "AKIAEXAMPLE", "secret_access_key": "super-secret-value"}
    ciphertext = cloud_credentials.encrypt_credentials(creds)
    assert "super-secret-value" not in ciphertext
    assert cloud_credentials.decrypt_credentials(ciphertext) == creds


def test_missing_key_raises_a_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "cloud_credentials_encryption_key", "")
    with pytest.raises(cloud_credentials.CloudCredentialsError, match="CLOUD_CREDENTIALS_ENCRYPTION_KEY"):
        cloud_credentials.encrypt_credentials({"a": "b"})


def test_malformed_key_raises_a_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "cloud_credentials_encryption_key", "not-a-real-fernet-key")
    with pytest.raises(cloud_credentials.CloudCredentialsError, match="not a valid Fernet key"):
        cloud_credentials.encrypt_credentials({"a": "b"})


def test_decrypt_with_a_different_key_fails_clearly():
    ciphertext = cloud_credentials.encrypt_credentials({"a": "b"})
    # simulate a rotated key - a fresh key generated between encrypt and decrypt
    import app.cloud_credentials as mod
    from unittest.mock import patch
    with patch.object(settings, "cloud_credentials_encryption_key", Fernet.generate_key().decode()):
        with pytest.raises(cloud_credentials.CloudCredentialsError, match="could not decrypt"):
            mod.decrypt_credentials(ciphertext)


def test_redact_scrubs_every_secret_value():
    text = "auth failed for key AKIA123 using secret sk-abc-999 against endpoint"
    redacted = cloud_credentials.redact(text, ["AKIA123", "sk-abc-999"])
    assert "AKIA123" not in redacted
    assert "sk-abc-999" not in redacted
    assert "auth failed" in redacted and "endpoint" in redacted


def test_redact_ignores_falsy_secrets():
    # empty-string / None secret values (e.g. an optional field never set)
    # must not raise, and only the real, non-empty secret gets redacted
    assert cloud_credentials.redact("hello world", ["", None, "world"]) == "hello ***"
