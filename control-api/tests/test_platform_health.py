"""Tests for the unified platform health rollup (routers/platform_health.py)
- one endpoint composing infrastructure/tickhouse/security/trading status
instead of an operator checking Topology, Metrics, and Audit log separately."""
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


def test_health_rollup_shape(client, tadmin):
    r = client.get("/platform/health", headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overall"] in ("healthy", "unknown", "live", "degraded", "critical")
    for key in ("infrastructure", "tickhouse", "security", "trading"):
        assert key in body["components"]
        assert "status" in body["components"][key]
        assert "detail" in body["components"][key]
    assert "checked_at" in body


def test_overall_reflects_worst_component(client, tadmin, monkeypatch):
    import app.routers.platform_health as ph
    monkeypatch.setattr(ph, "_infrastructure_component", lambda: {"status": "critical", "detail": "x"})
    monkeypatch.setattr(ph, "_tickhouse_component", lambda: {"status": "healthy", "detail": "x"})
    monkeypatch.setattr(ph, "_trading_component", lambda: {"status": "healthy", "detail": "x"})
    r = client.get("/platform/health", headers=tadmin)
    assert r.json()["overall"] == "critical"


def test_platform_admin_without_tenant_scope_is_rejected(client):
    r = client.post("/auth/login", json={"email": "admin@platform.local", "password": "changeme"})
    padmin = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r2 = client.get("/platform/health", headers=padmin)
    assert r2.status_code == 400
