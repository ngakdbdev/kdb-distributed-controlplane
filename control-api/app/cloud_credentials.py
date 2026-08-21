"""
cloud_credentials.py - encrypt/decrypt the raw cloud credentials
CloudProvisionRun.credentials_encrypted holds (see that model's own
docstring for the full trust-model reasoning). This is the ONLY place in
this codebase that handles raw AWS/Azure/GCP account credentials - every
other cloud-touching path (export_sinks.py's S3 uploads, InfraProfile /
fleet_agent) deliberately uses ambient identity (the host/pod's own IAM
role or service account) instead, exactly to avoid needing a module like
this at all. This one exists because bootstrapping brand-new
infrastructure via terraform genuinely has no ambient identity to lean
on yet - there's no cluster/role to inherit before the cluster exists.

Fernet (symmetric, authenticated encryption - cryptography.fernet) keyed by
CLOUD_CREDENTIALS_ENCRYPTION_KEY, same "one required secret in .env, no
built-in insecure fallback that's easy to forget to change" posture as
JWT_SECRET, except this one refuses to start at all with the dev default
(see key() below) rather than silently running insecurely - unlike a
forgeable session token, a leaked cloud credential is a direct path to a
real AWS/Azure/GCP account, not something to risk defaulting open on.
"""
from __future__ import annotations

import base64
import json

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


class CloudCredentialsError(RuntimeError):
    pass


def _key() -> bytes:
    raw = settings.cloud_credentials_encryption_key
    if not raw:
        raise CloudCredentialsError(
            "CLOUD_CREDENTIALS_ENCRYPTION_KEY is not set - required before any cloud "
            "auto-provisioning credentials can be stored or read. Generate one with "
            "`python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` "
            "and set it in .env. There is no insecure default for this one - a leaked "
            "cloud credential is a direct path into a real AWS/Azure/GCP account."
        )
    try:
        # Fernet keys are already base64 - validate it's actually one rather
        # than let a malformed value fail confusingly deep inside encrypt().
        key_bytes = raw.encode("utf-8")
        base64.urlsafe_b64decode(key_bytes)
        return key_bytes
    except Exception as exc:
        raise CloudCredentialsError(
            "CLOUD_CREDENTIALS_ENCRYPTION_KEY is not a valid Fernet key - generate one with "
            "`python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`"
        ) from exc


def encrypt_credentials(creds: dict) -> str:
    """dict -> opaque ciphertext string, safe to store in
    CloudProvisionRun.credentials_encrypted. `creds` is never logged here or
    by any caller - see cloud_provisioner.py's own handling."""
    f = Fernet(_key())
    payload = json.dumps(creds, separators=(",", ":")).encode("utf-8")
    return f.encrypt(payload).decode("utf-8")


def decrypt_credentials(ciphertext: str) -> dict:
    """Inverse of encrypt_credentials(). Raises CloudCredentialsError (not
    Fernet's own InvalidToken) on a bad/rotated key or corrupted value, so
    callers have one exception type to handle regardless of which of
    several things went wrong underneath."""
    f = Fernet(_key())
    try:
        payload = f.decrypt(ciphertext.encode("utf-8"))
    except InvalidToken as exc:
        raise CloudCredentialsError(
            "could not decrypt stored credentials - CLOUD_CREDENTIALS_ENCRYPTION_KEY may have "
            "changed since these were saved, or the stored value is corrupted"
        ) from exc
    return json.loads(payload)


def redact(text: str, secrets: list) -> str:
    """Scrub any of `secrets` (raw credential values) out of `text` before
    it's stored in CloudProvisionRun.log_tail/error_detail or returned by
    any API response - defense in depth in case a credential value ever
    ends up echoed into terraform/helm's own stdout/stderr (e.g. in a
    provider auth error message), same principle as fleet_agent/
    kx_installer.py's own token-scrubbing of subprocess output."""
    out = text
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    return out
