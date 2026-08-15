"""Tests for product licensing: 32-char keys, trial expiry, tamper detection."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import licensing


# ---- key format + roundtrip ----------------------------------------------

def test_minted_key_is_32_chars():
    assert len(licensing.mint(edition="standard", valid_days=365)) == 32
    assert len(licensing.mint_trial()) == 32


def test_mint_then_validate_roundtrip():
    key = licensing.mint(edition="enterprise", valid_days=365)
    info = licensing.validate(key)
    assert info.valid and info.edition == "enterprise"
    assert info.days_remaining > 360


def test_key_accepts_hyphen_and_case_insensitive_input():
    key = licensing.mint_trial()
    grouped = "-".join([key[i:i+8] for i in range(0, 32, 8)]).lower()
    assert licensing.validate(grouped).valid


# ---- trial ---------------------------------------------------------------

def test_trial_expires_after_30_days():
    issued = date(2026, 1, 1)
    key = licensing.mint_trial(issued=issued)
    assert licensing.validate(key, today=issued).is_trial
    assert licensing.validate(key, today=issued + timedelta(days=29)).valid
    # day 30 is the last valid day; day 31 is expired
    assert licensing.validate(key, today=issued + timedelta(days=30)).valid
    expired = licensing.validate(key, today=issued + timedelta(days=31))
    assert not expired.valid and "expired" in expired.reason


def test_days_remaining_counts_down():
    issued = date(2026, 1, 1)
    key = licensing.mint_trial(issued=issued)
    assert licensing.validate(key, today=issued + timedelta(days=10)).days_remaining == 20


# ---- tamper / invalid ----------------------------------------------------

def test_tampered_key_fails_signature():
    key = licensing.mint(edition="standard", valid_days=365)
    # flip a character in the payload region
    bad = ("A" if key[0] != "A" else "B") + key[1:]
    info = licensing.validate(bad)
    assert not info.valid

def test_wrong_length_and_garbage_rejected():
    assert not licensing.validate("").valid
    assert not licensing.validate("TOO-SHORT").valid
    assert not licensing.validate("!" * 32).valid

def test_key_from_other_secret_does_not_validate(monkeypatch):
    monkeypatch.setenv("LICENSE_SIGNING_SECRET", "secret-A")
    key = licensing.mint(edition="standard", valid_days=365)
    monkeypatch.setenv("LICENSE_SIGNING_SECRET", "secret-B")
    assert not licensing.validate(key).valid


# ---- endpoint ------------------------------------------------------------

@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def token(client):
    r = client.post("/auth/login", json={"email": "admin@platform.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_license_endpoint_reports_status(client, token, monkeypatch):
    monkeypatch.setenv("LICENSE_KEY", licensing.mint_trial())
    r = client.get("/license", headers=token)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] is True and body["trial"] is True and body["edition"] == "trial"


def test_license_endpoint_flags_missing_key(client, token, monkeypatch):
    monkeypatch.delenv("LICENSE_KEY", raising=False)
    r = client.get("/license", headers=token)
    assert r.json()["valid"] is False


# ---- enforcement policy (DEPLOYMENT_ENV / LICENSE_ENFORCE) ----------------
# "license key is mandatory" for anything that isn't the developer's own
# laptop: local/dev deployments stay unenforced with zero configuration,
# customer-facing ones (DEPLOYMENT_ENV=customer - cloud VM scripts, the Helm
# chart, fleet_agent) are enforced by default. LICENSE_ENFORCE, if set at
# all, always wins over the DEPLOYMENT_ENV-derived default either way.

def test_local_deployment_env_is_unenforced_by_default():
    assert licensing.enforcement_active(deployment_env="local", explicit="") is False

def test_dev_and_development_are_also_treated_as_local():
    assert licensing.enforcement_active(deployment_env="dev", explicit="") is False
    assert licensing.enforcement_active(deployment_env="development", explicit="") is False

def test_customer_deployment_env_is_enforced_by_default():
    assert licensing.enforcement_active(deployment_env="customer", explicit="") is True

def test_unrecognized_deployment_env_defaults_to_enforced():
    # fail toward "mandatory", not toward "unenforced", for anything that
    # isn't explicitly recognized as local/dev - a typo in DEPLOYMENT_ENV
    # must never silently disable the requirement.
    assert licensing.enforcement_active(deployment_env="staging", explicit="") is True
    assert licensing.enforcement_active(deployment_env="", explicit="") is True

def test_explicit_override_wins_over_customer_default():
    assert licensing.enforcement_active(deployment_env="customer", explicit="false") is False

def test_explicit_override_wins_over_local_default():
    assert licensing.enforcement_active(deployment_env="local", explicit="true") is True

def test_deployment_env_is_case_insensitive():
    assert licensing.enforcement_active(deployment_env="CUSTOMER", explicit="") is True
    assert licensing.enforcement_active(deployment_env="Local", explicit="") is False

def test_no_args_reads_from_real_environment(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_ENV", "customer")
    monkeypatch.delenv("LICENSE_ENFORCE", raising=False)
    assert licensing.enforcement_active() is True
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    assert licensing.enforcement_active() is False
    monkeypatch.setenv("LICENSE_ENFORCE", "false")
    assert licensing.enforcement_active() is False
    monkeypatch.setenv("DEPLOYMENT_ENV", "customer")
    assert licensing.enforcement_active() is False  # explicit override still wins


def test_startup_refuses_with_invalid_license_in_customer_env(monkeypatch):
    monkeypatch.delenv("LICENSE_KEY", raising=False)
    monkeypatch.setenv("DEPLOYMENT_ENV", "customer")
    monkeypatch.delenv("LICENSE_ENFORCE", raising=False)
    with pytest.raises(RuntimeError, match="licence key is mandatory"):
        m._check_license()


def test_startup_does_not_refuse_in_local_env(monkeypatch):
    monkeypatch.delenv("LICENSE_KEY", raising=False)
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.delenv("LICENSE_ENFORCE", raising=False)
    m._check_license()  # must not raise


def test_startup_succeeds_with_valid_license_in_customer_env(monkeypatch):
    monkeypatch.setenv("LICENSE_KEY", licensing.mint(edition="standard", valid_days=365))
    monkeypatch.setenv("DEPLOYMENT_ENV", "customer")
    monkeypatch.delenv("LICENSE_ENFORCE", raising=False)
    m._check_license()  # must not raise
