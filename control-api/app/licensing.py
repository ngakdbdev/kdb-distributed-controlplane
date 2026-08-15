"""
licensing.py - the product's own licensing (separate from KX's licence).

A licence is a 32-character key that is tamper-evident and carries its own
expiry, so it validates fully offline (air-gap friendly, no phone-home):

    20 bytes = 8-byte payload + 12-byte HMAC-SHA256(payload, secret)
    base32(20 bytes) -> exactly 32 chars, no padding

payload = version(1) | edition(1) | issued_days(2) | expiry_days(2) | flags(2)
(days are counted from 2020-01-01). A trial is just edition=trial with
expiry = issued + 30 days.

Signing is HMAC with a product secret (LICENSE_SIGNING_SECRET, or the built-in
default for local/dev). That makes keys unforgeable WITHOUT the secret; note the
honest trade-off - anyone who extracts the secret from a deployment could mint
keys. For stronger anti-forgery you'd move to asymmetric signatures (longer
keys) or online activation; this is the pragmatic choice for a fixed 32-char key.
"""
from __future__ import annotations

import base64
import os
import hmac
import os
import struct
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256

_EPOCH = date(2020, 1, 1)
_VERSION = 1
_PAYLOAD_FMT = ">BBHHH"          # version, edition, issued_days, expiry_days, flags
_PAYLOAD_LEN = 8
_SIG_LEN = 12
TRIAL_DAYS = 30

EDITIONS = {0: "trial", 1: "standard", 2: "enterprise"}
EDITION_IDS = {v: k for k, v in EDITIONS.items()}

# Default signing secret for local/dev. Override in production so keys minted
# elsewhere don't validate against your deployment.
_DEFAULT_SECRET = b"kdb-control-plane-product-signing-key-CHANGE-ME"


def _secret() -> bytes:
    env = os.environ.get("LICENSE_SIGNING_SECRET")
    return env.encode() if env else _DEFAULT_SECRET


def _days(d: date) -> int:
    return (d - _EPOCH).days


def _date_from_days(n: int) -> date:
    return _EPOCH + timedelta(days=n)


@dataclass
class LicenseInfo:
    valid: bool
    reason: str = ""
    edition: str = ""
    issued: date | None = None
    expiry: date | None = None
    days_remaining: int = 0

    @property
    def is_trial(self) -> bool:
        return self.edition == "trial"


def mint(edition: str = "standard", valid_days: int = 365,
         issued: date | None = None, flags: int = 0) -> str:
    """Create a 32-char licence key."""
    if edition not in EDITION_IDS:
        raise ValueError(f"unknown edition '{edition}', want one of {sorted(EDITION_IDS)}")
    issued = issued or date.today()
    expiry = issued + timedelta(days=valid_days)
    payload = struct.pack(_PAYLOAD_FMT, _VERSION, EDITION_IDS[edition],
                          _days(issued), _days(expiry), flags)
    sig = hmac.new(_secret(), payload, sha256).digest()[:_SIG_LEN]
    return base64.b32encode(payload + sig).decode("ascii")


def mint_trial(issued: date | None = None) -> str:
    """A trial key that expires 30 days after issue."""
    return mint(edition="trial", valid_days=TRIAL_DAYS, issued=issued)


def validate(key: str, today: date | None = None) -> LicenseInfo:
    """Validate a key fully offline. Never raises - returns LicenseInfo."""
    today = today or date.today()
    if not key:
        return LicenseInfo(valid=False, reason="no licence key")
    key = key.strip().replace("-", "").replace(" ", "").upper()
    if len(key) != 32:
        return LicenseInfo(valid=False, reason="key must be 32 characters")
    try:
        raw = base64.b32decode(key)
    except (ValueError, Exception):  # noqa: BLE001
        return LicenseInfo(valid=False, reason="key is not valid base32")
    if len(raw) != _PAYLOAD_LEN + _SIG_LEN:
        return LicenseInfo(valid=False, reason="key has the wrong length")

    payload, sig = raw[:_PAYLOAD_LEN], raw[_PAYLOAD_LEN:]
    expected = hmac.new(_secret(), payload, sha256).digest()[:_SIG_LEN]
    if not hmac.compare_digest(sig, expected):
        return LicenseInfo(valid=False, reason="signature check failed (tampered or wrong secret)")

    version, edition_id, issued_days, expiry_days, _flags = struct.unpack(_PAYLOAD_FMT, payload)
    if version != _VERSION:
        return LicenseInfo(valid=False, reason=f"unsupported licence version {version}")
    edition = EDITIONS.get(edition_id, "unknown")
    issued = _date_from_days(issued_days)
    expiry = _date_from_days(expiry_days)
    remaining = (expiry - today).days
    if remaining < 0:
        return LicenseInfo(valid=False, reason=f"licence expired on {expiry.isoformat()}",
                           edition=edition, issued=issued, expiry=expiry, days_remaining=remaining)
    return LicenseInfo(valid=True, reason="ok", edition=edition,
                       issued=issued, expiry=expiry, days_remaining=remaining)


def status_line(info: LicenseInfo) -> str:
    if not info.valid:
        return f"LICENCE INVALID: {info.reason}"
    warn = "  (expiring soon!)" if info.days_remaining <= 7 else ""
    return (f"licence ok: {info.edition}, expires {info.expiry.isoformat()} "
            f"({info.days_remaining} days left){warn}")


# --------------------------------------------------------------------------- enforcement policy
# A licence key is mandatory for any deployment that isn't the developer's
# own local box: DEPLOYMENT_ENV distinguishes the two. Local/dev keeps
# working with zero configuration (the default, `docker compose up` on a
# laptop needs no key) - the mandatory requirement kicks in for anything
# that ships to a customer (the cloud VM scripts set DEPLOYMENT_ENV=customer,
# the Helm chart defaults it the same way, fleet_agent runs inside a
# tenant's own environment by definition). LICENSE_ENFORCE, if set at all,
# always wins over the DEPLOYMENT_ENV-derived default in either direction -
# it's the explicit escape hatch for the rare case (testing the enforcement
# path locally, or deliberately running a customer box unenforced during a
# support incident) where the default for that environment isn't what's
# wanted right now.
_LOCAL_DEPLOYMENT_ENVS = ("local", "dev", "development")


def enforcement_active(deployment_env: str | None = None, explicit: str | None = None) -> bool:
    """Whether main.py should refuse to start on an invalid/missing licence.
    Both args default to `None`, meaning "read it from the environment
    myself" (DEPLOYMENT_ENV / LICENSE_ENFORCE) - callers can still pass
    them explicitly, which is what makes this unit-testable without env-var
    monkeypatching gymnastics."""
    if explicit is None:
        explicit = os.environ.get("LICENSE_ENFORCE", "")
    if explicit:
        return explicit.strip().lower() in ("1", "true", "yes")
    if deployment_env is None:
        deployment_env = os.environ.get("DEPLOYMENT_ENV", "local")
    return deployment_env.strip().lower() not in _LOCAL_DEPLOYMENT_ENVS
