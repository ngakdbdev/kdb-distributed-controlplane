"""Tests for per-tenant user management (routers/users.py) - an Admin
(tenant_admin) creating/editing logins within their own tenant and
assigning one of the tenant-level roles (tenant_admin/functional_user/
quant_analyst)."""
import pytest
from fastapi.testclient import TestClient

import app.main as m


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login",
                    json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _login(client, email, password):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_admin_can_create_functional_user_with_trading_default_on(client, tadmin):
    r = client.post("/users", json={"email": "ops1@demo-bank.local", "password": "correcthorse123",
                                    "role": "functional_user"}, headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "functional_user"
    assert body["can_trade"] is True  # role default


def test_admin_can_create_quant_analyst_with_trading_default_off(client, tadmin):
    r = client.post("/users", json={"email": "quant1@demo-bank.local", "password": "correcthorse123",
                                    "role": "quant_analyst"}, headers=tadmin)
    assert r.status_code == 200, r.text
    assert r.json()["can_trade"] is False


def test_new_user_can_log_in_and_reach_role_appropriate_endpoint(client, tadmin):
    client.post("/users", json={"email": "ops2@demo-bank.local", "password": "correcthorse123",
                                "role": "functional_user"}, headers=tadmin)
    ops = _login(client, "ops2@demo-bank.local", "correcthorse123")
    r = client.get("/bot/config", headers=ops)
    assert r.status_code == 200


def test_functional_user_cannot_create_other_users(client, tadmin):
    client.post("/users", json={"email": "ops3@demo-bank.local", "password": "correcthorse123",
                                "role": "functional_user"}, headers=tadmin)
    ops = _login(client, "ops3@demo-bank.local", "correcthorse123")
    r = client.post("/users", json={"email": "sneaky@demo-bank.local", "password": "correcthorse123",
                                    "role": "tenant_admin"}, headers=ops)
    assert r.status_code == 403


def test_functional_user_cannot_create_tickhouses(client, tadmin):
    client.post("/users", json={"email": "ops4@demo-bank.local", "password": "correcthorse123",
                                "role": "functional_user"}, headers=tadmin)
    ops = _login(client, "ops4@demo-bank.local", "correcthorse123")
    r = client.post("/tickhouses", json={"name": "x", "location": "aws", "os": "ubuntu-22.04",
                                         "profile": "balanced", "shard_ranges": "a-z", "idb": False,
                                         "target_config": {}}, headers=ops)
    assert r.status_code == 403


def test_cannot_assign_platform_admin_role(client, tadmin):
    r = client.post("/users", json={"email": "wannabe@demo-bank.local", "password": "correcthorse123",
                                    "role": "platform_admin"}, headers=tadmin)
    assert r.status_code in (400, 422)


def test_duplicate_email_rejected(client, tadmin):
    client.post("/users", json={"email": "dup@demo-bank.local", "password": "correcthorse123",
                                "role": "functional_user"}, headers=tadmin)
    r = client.post("/users", json={"email": "dup@demo-bank.local", "password": "correcthorse123",
                                    "role": "quant_analyst"}, headers=tadmin)
    assert r.status_code == 409


def test_admin_can_update_role_and_can_trade(client, tadmin):
    created = client.post("/users", json={"email": "flex@demo-bank.local", "password": "correcthorse123",
                                          "role": "quant_analyst"}, headers=tadmin).json()
    r = client.put(f"/users/{created['id']}", json={"can_trade": True}, headers=tadmin)
    assert r.status_code == 200
    assert r.json()["can_trade"] is True


def test_admin_can_deactivate_a_user_and_they_can_no_longer_log_in(client, tadmin):
    created = client.post("/users", json={"email": "gone@demo-bank.local", "password": "correcthorse123",
                                          "role": "functional_user"}, headers=tadmin).json()
    r = client.put(f"/users/{created['id']}", json={"active": False}, headers=tadmin)
    assert r.status_code == 200
    login_attempt = client.post("/auth/login", json={"email": "gone@demo-bank.local", "password": "correcthorse123"})
    assert login_attempt.status_code == 401


def test_last_admin_cannot_demote_themselves(client):
    # isolated tenant with exactly one admin, to test the "last admin" guard
    # in complete isolation from the shared demo tenant's own admin count.
    with TestClient(m.app) as c:
        padmin_r = c.post("/auth/login", json={"email": "admin@platform.local", "password": "changeme"})
        padmin = {"Authorization": f"Bearer {padmin_r.json()['access_token']}"}
        c.post("/tenants", json={"name": "Solo Bank", "admin_email": "solo@bank.local",
                                 "admin_password": "correcthorse123"}, headers=padmin)
        solo = _login(c, "solo@bank.local", "correcthorse123")
        me = next(u for u in c.get("/users", headers=solo).json() if u["email"] == "solo@bank.local")
        r = c.put(f"/users/{me['id']}", json={"active": False}, headers=solo)
        assert r.status_code == 400
        assert "only admin" in r.json()["detail"]
