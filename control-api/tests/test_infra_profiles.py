"""Tests for the per-tenant infra-profile settings (routers/infra_profiles.py)
- reusable, non-secret cloud/k8s coordinate bundles a tenant's own admin
manages, and any authenticated user in that tenant can read (needed to pick
one while creating a TickHouse). Tenant-scoped like TickHouse/Agent - each
tenant deploys into its own cloud accounts."""
import pytest
from fastapi.testclient import TestClient

import app.main as m


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def padmin(client):
    r = client.post("/auth/login", json={"email": "admin@platform.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login",
                    json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _aws_body(name="AWS Production", is_default=False):
    return {"name": name, "description": "prod account", "provider": "aws",
            "config": {"region": "us-east-1", "vpc_id": "vpc-123", "subnet_ids": "subnet-1,subnet-2",
                       "eks_cluster": "prod", "namespace": "tick", "storage_class": "gp3",
                       "ingress_class": "nginx"},
            "is_default": is_default, "enabled": True}


def test_meta_lists_providers_and_fields(client, tadmin):
    r = client.get("/infra-profiles/meta", headers=tadmin)
    assert r.status_code == 200
    body = r.json()
    assert "aws" in body["providers"]
    assert "region" in body["config_fields"]["aws"]


def test_platform_admin_cannot_manage_tenant_infra_profiles(client, padmin):
    # platform_admin has no tenant_id - creating/managing a TENANT's own
    # infra profiles isn't the SaaS operator's job (see require_admin).
    r = client.post("/infra-profiles", json=_aws_body(), headers=padmin)
    assert r.status_code == 400


def test_tenant_admin_can_create_and_read_it(client, tadmin):
    r = client.post("/infra-profiles", json=_aws_body("AWS Prod A"), headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "aws" and body["config"]["region"] == "us-east-1"

    r2 = client.get("/infra-profiles", headers=tadmin)
    assert r2.status_code == 200
    names = [p["name"] for p in r2.json()]
    assert "AWS Prod A" in names


def test_unknown_field_for_provider_is_rejected(client, tadmin):
    body = _aws_body("Bad Profile")
    body["config"]["totally_made_up_field"] = "x"
    r = client.post("/infra-profiles", json=body, headers=tadmin)
    assert r.status_code == 400
    assert "totally_made_up_field" in r.json()["detail"]


def test_unknown_provider_is_rejected(client, tadmin):
    body = _aws_body("Bad Provider")
    body["provider"] = "oracle-cloud"
    r = client.post("/infra-profiles", json=body, headers=tadmin)
    assert r.status_code == 400


def test_setting_a_new_default_demotes_the_old_one(client, tadmin):
    r1 = client.post("/infra-profiles", json=_aws_body("AWS Old Default", is_default=True), headers=tadmin)
    id1 = r1.json()["id"]
    assert r1.json()["is_default"] is True

    r2 = client.post("/infra-profiles", json=_aws_body("AWS New Default", is_default=True), headers=tadmin)
    assert r2.json()["is_default"] is True

    r1_after = client.get("/infra-profiles", headers=tadmin).json()
    old = next(p for p in r1_after if p["id"] == id1)
    assert old["is_default"] is False


def test_update_and_delete_roundtrip(client, tadmin):
    created = client.post("/infra-profiles", json=_aws_body("AWS Temp"), headers=tadmin).json()
    pid = created["id"]

    updated_body = _aws_body("AWS Temp Renamed")
    updated_body["config"]["region"] = "eu-west-1"
    r = client.put(f"/infra-profiles/{pid}", json=updated_body, headers=tadmin)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "AWS Temp Renamed"
    assert r.json()["config"]["region"] == "eu-west-1"

    r_del = client.delete(f"/infra-profiles/{pid}", headers=tadmin)
    assert r_del.status_code == 200

    remaining = client.get("/infra-profiles", headers=tadmin).json()
    assert pid not in [p["id"] for p in remaining]


def test_delete_missing_profile_404s(client, tadmin):
    r = client.delete("/infra-profiles/999999", headers=tadmin)
    assert r.status_code == 404
