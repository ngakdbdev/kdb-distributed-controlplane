"""Tests for app/cloud_provisioner.py's credential-free logic: validation,
plan(), tfvars shaping, and run()'s guard rails. apply()'s actual subprocess
calls are exercised here only through run() with subprocess.run mocked out -
no real terraform/helm/cloud calls, same "plan() is secret-free and
unit-tested, the real execution isn't run in CI" split as fleet_agent/
kx_installer.py."""
import json
from unittest.mock import patch, MagicMock

import pytest
from cryptography.fernet import Fernet

from app import cloud_provisioner
from app.config import settings


@pytest.fixture(autouse=True)
def _real_key(monkeypatch):
    monkeypatch.setattr(settings, "cloud_credentials_encryption_key", Fernet.generate_key().decode())


# ---- validate_credentials ---------------------------------------------------

def test_aws_requires_both_keys():
    problems = cloud_provisioner.validate_credentials("aws", {"access_key_id": "AKIA"})
    assert any("secret_access_key" in p for p in problems)


def test_aws_valid_credentials_have_no_problems():
    assert cloud_provisioner.validate_credentials(
        "aws", {"access_key_id": "AKIA", "secret_access_key": "s3cr3t"}) == []


def test_azure_requires_all_four_fields():
    problems = cloud_provisioner.validate_credentials("azure", {"tenant_id": "t"})
    assert any("client_id" in p for p in problems)
    assert any("client_secret" in p for p in problems)
    assert any("subscription_id" in p for p in problems)


def test_gcp_requires_valid_service_account_json():
    problems = cloud_provisioner.validate_credentials("gcp", {"service_account_json": "not json"})
    assert any("not valid JSON" in p for p in problems)


def test_gcp_rejects_json_that_is_not_a_service_account():
    bad = json.dumps({"type": "authorized_user"})
    problems = cloud_provisioner.validate_credentials("gcp", {"service_account_json": bad})
    assert any("service account" in p for p in problems)


def test_gcp_accepts_real_looking_service_account_json():
    good = json.dumps({"type": "service_account", "project_id": "p", "private_key": "x"})
    assert cloud_provisioner.validate_credentials("gcp", {"service_account_json": good}) == []


def test_unknown_provider_is_rejected():
    problems = cloud_provisioner.validate_credentials("digitalocean", {})
    assert any("unknown provider" in p for p in problems)


# ---- plan() - credential-free -----------------------------------------------

def test_plan_never_contains_a_credential_value():
    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme", provider="aws", region="us-east-1")
    flat = " ".join(" ".join(str(a) for a in argv) for _label, argv in cloud_provisioner.plan(req))
    assert "SECRET_VALUE_SHOULD_NEVER_APPEAR" not in flat  # sanity: nothing plan() does touches real creds


def test_plan_covers_the_whole_pipeline():
    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme", provider="aws", region="us-east-1")
    labels = [label for label, _ in cloud_provisioner.plan(req)]
    assert "terraform init" in labels
    assert "terraform apply" in labels
    assert "helm install" in labels


def test_plan_rejects_unknown_cluster_profile():
    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme", provider="aws",
                                             region="us-east-1", cluster_profile="ultra")
    with pytest.raises(cloud_provisioner.ProvisionError, match="cluster_profile"):
        cloud_provisioner.plan(req)


# ---- tfvars shaping ----------------------------------------------------------

def test_aws_tfvars_uses_region_field():
    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme", provider="aws", region="us-east-1")
    text = cloud_provisioner._tfvars_text(req)
    assert 'region = "us-east-1"' in text
    assert "location" not in text


def test_azure_tfvars_uses_location_field():
    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme", provider="azure", region="eastus")
    text = cloud_provisioner._tfvars_text(req)
    assert 'location = "eastus"' in text
    assert 'region = "eastus"' not in text


def test_gcp_tfvars_includes_project_id():
    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme", provider="gcp",
                                             region="us-central1", project_id="my-proj")
    text = cloud_provisioner._tfvars_text(req)
    assert 'project_id = "my-proj"' in text


def test_tfvars_never_contains_a_secret():
    # tfvars is written to disk - credentials must never reach it, only
    # subprocess env (_credential_env), regardless of provider
    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme", provider="aws", region="us-east-1")
    assert "AKIA" not in cloud_provisioner._tfvars_text(req)


# ---- credential -> env var mapping -------------------------------------------

def test_aws_credential_env_maps_to_standard_aws_vars():
    env = cloud_provisioner._credential_env("aws", {"access_key_id": "AKIA1", "secret_access_key": "s3cr3t"})
    assert env == {"AWS_ACCESS_KEY_ID": "AKIA1", "AWS_SECRET_ACCESS_KEY": "s3cr3t"}


def test_azure_credential_env_maps_to_standard_arm_vars():
    env = cloud_provisioner._credential_env("azure", {
        "tenant_id": "t", "client_id": "c", "client_secret": "s", "subscription_id": "sub"})
    assert env == {"ARM_TENANT_ID": "t", "ARM_CLIENT_ID": "c",
                   "ARM_CLIENT_SECRET": "s", "ARM_SUBSCRIPTION_ID": "sub"}


def test_gcp_credential_env_points_at_a_file_path_not_inline_json():
    env = cloud_provisioner._credential_env("gcp", {"_service_account_json_path": "/tmp/sa.json"})
    assert env == {"GOOGLE_APPLICATION_CREDENTIALS": "/tmp/sa.json"}


# ---- run() guard rails --------------------------------------------------------

def test_run_refuses_without_the_exact_confirm_phrase():
    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme", provider="aws", region="us-east-1")
    with pytest.raises(cloud_provisioner.ProvisionError, match="confirm_ack"):
        cloud_provisioner.run(1, req, {"access_key_id": "a", "secret_access_key": "b"},
                              "close-but-not-it", lambda *a: None)


def test_run_refuses_invalid_credentials_even_with_correct_confirm_phrase():
    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme", provider="aws", region="us-east-1")
    with pytest.raises(cloud_provisioner.ProvisionError, match="invalid credentials"):
        cloud_provisioner.run(1, req, {"access_key_id": ""}, cloud_provisioner.CONFIRM_PHRASE, lambda *a: None)


def test_run_never_shells_out_when_guard_rails_reject_it():
    """Confirms the guard-rail checks happen BEFORE any subprocess is
    touched - a wrong confirm_ack must never risk even starting terraform."""
    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme", provider="aws", region="us-east-1")
    with patch("subprocess.run") as mock_run:
        with pytest.raises(cloud_provisioner.ProvisionError):
            cloud_provisioner.run(1, req, {"access_key_id": "a", "secret_access_key": "b"},
                                  "wrong", lambda *a: None)
        mock_run.assert_not_called()


def test_run_reports_terraform_init_failure_without_raising(tmp_path, monkeypatch):
    """A terraform failure is a normal, expected outcome (bad credentials,
    quota limits, ...) - run() must return {"ok": False, ...}, not raise,
    so the background-thread caller can record it as a failed run rather
    than crash the thread."""
    monkeypatch.setattr(cloud_provisioner, "RUNS_DIR", tmp_path)
    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme", provider="aws", region="us-east-1")
    progress_calls = []

    fake_proc = MagicMock(returncode=1, stdout="", stderr="Error: NoCredentialProviders")
    with patch("shutil.copytree", side_effect=lambda src, dst: dst.mkdir(parents=True) or None), \
         patch("pathlib.Path.write_text"), \
         patch("subprocess.run", return_value=fake_proc):
        result = cloud_provisioner.run(1, req, {"access_key_id": "a", "secret_access_key": "b"},
                                       cloud_provisioner.CONFIRM_PHRASE,
                                       lambda status, detail, log: progress_calls.append((status, detail)))

    assert result["ok"] is False
    assert "terraform init failed" in result["error"]
    assert ("applying", "terraform init") in [(s, d) for s, d in progress_calls]


def test_run_scrubs_the_secret_from_a_failure_message(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_provisioner, "RUNS_DIR", tmp_path)
    req = cloud_provisioner.ProvisionRequest(tenant_id=1, name="acme", provider="aws", region="us-east-1")
    secret = "AKIA-SHOULD-NEVER-LEAK"

    fake_proc = MagicMock(returncode=1, stdout="", stderr=f"auth failed with key {secret}")
    with patch("shutil.copytree", side_effect=lambda src, dst: dst.mkdir(parents=True) or None), \
         patch("pathlib.Path.write_text"), \
         patch("subprocess.run", return_value=fake_proc):
        result = cloud_provisioner.run(1, req, {"access_key_id": secret, "secret_access_key": "b"},
                                       cloud_provisioner.CONFIRM_PHRASE, lambda *a: None)

    assert secret not in result["error"]
    assert "***" in result["error"]
