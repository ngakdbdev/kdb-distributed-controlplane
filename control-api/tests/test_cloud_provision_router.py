"""Tests for routers/cloud_provision.py - the credentials-only auto-
provisioning API. The actual terraform/helm execution (cloud_provisioner.run)
is monkeypatched throughout - these tests exercise the HTTP surface, the
confirm_ack/validation guard rails, encrypted storage, and the
background-thread completion callback's DB update logic, never a real
subprocess."""
import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import app.main as m
from app import cloud_provisioner
from app.config import settings
from app.db import engine
from app.models import CloudProvisionRun
from app.routers import cloud_provision as cp_router
from sqlmodel import Session


@pytest.fixture(autouse=True)
def _real_key(monkeypatch):
    monkeypatch.setattr(settings, "cloud_credentials_encryption_key", Fernet.generate_key().decode())


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


AWS_BODY = {
    "name": "acme-test", "region": "us-east-1", "cluster_profile": "cost_optimized",
    "access_key_id": "AKIAEXAMPLE", "secret_access_key": "s3cr3t-value",
    "confirm_ack": cloud_provisioner.CONFIRM_PHRASE,
}


@pytest.fixture(autouse=True)
def _no_real_background_thread(monkeypatch):
    """Every test in this file replaces the actual thread spawn with a
    no-op - none of these tests want a real background thread touching
    subprocess/terraform. Tests that need to verify the completion path
    call _execute_in_background directly and synchronously instead (see
    below), rather than relying on timing against a real thread."""
    monkeypatch.setattr(cp_router.threading, "Thread",
                        lambda target, args, daemon, name: type("FakeThread", (), {"start": lambda self: None})())


def test_aws_provision_rejects_wrong_confirm_ack(client, tadmin):
    body = {**AWS_BODY, "confirm_ack": "not-the-phrase"}
    r = client.post("/cloud-provision/aws", headers=tadmin, json=body)
    assert r.status_code == 400
    assert "confirm_ack" in r.json()["detail"]


def test_aws_provision_rejects_missing_credentials(client, tadmin):
    body = {**AWS_BODY, "secret_access_key": ""}
    r = client.post("/cloud-provision/aws", headers=tadmin, json=body)
    assert r.status_code == 400
    assert "invalid credentials" in r.json()["detail"]


def test_aws_provision_rejects_unknown_cluster_profile(client, tadmin):
    body = {**AWS_BODY, "cluster_profile": "extreme"}
    r = client.post("/cloud-provision/aws", headers=tadmin, json=body)
    assert r.status_code == 400
    assert "cluster_profile" in r.json()["detail"]


def test_aws_provision_creates_a_pending_run_and_never_returns_credentials(client, tadmin):
    r = client.post("/cloud-provision/aws", headers=tadmin, json=AWS_BODY)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["provider"] == "aws"
    assert body["name"] == "acme-test"
    assert "credentials_encrypted" not in body
    assert "AKIAEXAMPLE" not in json.dumps(body)
    assert "s3cr3t-value" not in json.dumps(body)


def test_aws_provision_stores_credentials_encrypted_not_plaintext(client, tadmin):
    r = client.post("/cloud-provision/aws", headers=tadmin, json=AWS_BODY)
    run_id = r.json()["id"]
    with Session(engine) as s:
        run = s.get(CloudProvisionRun, run_id)
        assert "AKIAEXAMPLE" not in run.credentials_encrypted
        assert "s3cr3t-value" not in run.credentials_encrypted
        # and it really does decrypt back to the real values
        from app import cloud_credentials
        decrypted = cloud_credentials.decrypt_credentials(run.credentials_encrypted)
        assert decrypted == {"access_key_id": "AKIAEXAMPLE", "secret_access_key": "s3cr3t-value"}


def test_azure_provision_requires_all_four_credential_fields(client, tadmin):
    # client_id="" is a valid string as far as pydantic's schema is
    # concerned - the "is this actually usable" check is
    # cloud_provisioner.validate_credentials, exercised here via the
    # endpoint, not a pydantic-level rejection.
    body = {"name": "acme-azure", "region": "eastus", "tenant_id": "t", "client_id": "",
           "client_secret": "s", "subscription_id": "sub", "confirm_ack": cloud_provisioner.CONFIRM_PHRASE}
    r = client.post("/cloud-provision/azure", headers=tadmin, json=body)
    assert r.status_code == 400
    assert "client_id" in r.json()["detail"]


def test_gcp_provision_rejects_malformed_service_account_json(client, tadmin):
    body = {"name": "acme-gcp", "region": "us-central1", "project_id": "p",
           "service_account_json": "not json", "confirm_ack": cloud_provisioner.CONFIRM_PHRASE}
    r = client.post("/cloud-provision/gcp", headers=tadmin, json=body)
    assert r.status_code == 400
    assert "not valid JSON" in r.json()["detail"]


def test_list_runs_only_shows_this_tenants_runs(client, tadmin):
    client.post("/cloud-provision/aws", headers=tadmin, json=AWS_BODY)
    r = client.get("/cloud-provision", headers=tadmin)
    assert r.status_code == 200, r.text
    assert all("credentials_encrypted" not in run for run in r.json())
    assert any(run["name"] == "acme-test" for run in r.json())


def test_get_run_404s_for_a_nonexistent_id(client, tadmin):
    r = client.get("/cloud-provision/999999999", headers=tadmin)
    assert r.status_code == 404


# ---- background completion path (called directly, no real thread/subprocess) --

def test_background_execution_marks_run_complete_on_success(client, tadmin, monkeypatch):
    r = client.post("/cloud-provision/aws", headers=tadmin, json=AWS_BODY)
    run_id = r.json()["id"]

    monkeypatch.setattr(cloud_provisioner, "run",
                        lambda run_id, req, creds, ack, on_progress: (
                            on_progress("applying", "terraform apply", "apply output"),
                            {"ok": True, "terraform_outputs": {"cluster_name": "acme-test-cluster"}, "error": ""}
                        )[1])

    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme-test", provider="aws", region="us-east-1")
    cp_router._execute_in_background(run_id, req, {"access_key_id": "a", "secret_access_key": "b"},
                                     cloud_provisioner.CONFIRM_PHRASE)

    with Session(engine) as s:
        run = s.get(CloudProvisionRun, run_id)
        assert run.status == "complete"
        assert json.loads(run.terraform_outputs_json) == {"cluster_name": "acme-test-cluster"}
        assert "apply output" in run.log_tail


def test_background_execution_marks_run_failed_with_error_detail(client, tadmin, monkeypatch):
    r = client.post("/cloud-provision/aws", headers=tadmin, json=AWS_BODY)
    run_id = r.json()["id"]

    monkeypatch.setattr(cloud_provisioner, "run",
                        lambda run_id, req, creds, ack, on_progress: {
                            "ok": False, "terraform_outputs": {}, "error": "terraform apply failed: quota exceeded"})

    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme-test", provider="aws", region="us-east-1")
    cp_router._execute_in_background(run_id, req, {"access_key_id": "a", "secret_access_key": "b"},
                                     cloud_provisioner.CONFIRM_PHRASE)

    with Session(engine) as s:
        run = s.get(CloudProvisionRun, run_id)
        assert run.status == "failed"
        assert "quota exceeded" in run.error_detail


def test_background_execution_handles_a_raised_provision_error(client, tadmin, monkeypatch):
    """cloud_provisioner.run() itself raising (e.g. the confirm_ack check)
    must still leave the run in a clean "failed" state, not crash the
    background thread silently."""
    r = client.post("/cloud-provision/aws", headers=tadmin, json=AWS_BODY)
    run_id = r.json()["id"]

    def _raise(*a, **kw):
        raise cloud_provisioner.ProvisionError("something went wrong")
    monkeypatch.setattr(cloud_provisioner, "run", _raise)

    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme-test", provider="aws", region="us-east-1")
    cp_router._execute_in_background(run_id, req, {"access_key_id": "a", "secret_access_key": "b"},
                                     cloud_provisioner.CONFIRM_PHRASE)

    with Session(engine) as s:
        run = s.get(CloudProvisionRun, run_id)
        assert run.status == "failed"
        assert "something went wrong" in run.error_detail
