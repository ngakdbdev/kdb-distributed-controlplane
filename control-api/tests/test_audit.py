"""Tests for GET /audit's tenant-visibility rules and POST /audit/internal
(the watchdog/tickerplant self-reporting path). The visibility rule is
narrower than "any tenant_id IS NULL row" - see routers/audit.py's own
docstring on why create_tenant/suspend_tenant (also tenant_id=None, but
actor is a human admin email) must stay platform-admin-only while the
watchdog's/a tickerplant's own reports (actor="watchdog" / "tp:*") are
safe to show every tenant on this deployment."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app.main as m
from app.config import settings
from app.db import engine
from app.models import AuditEvent, Tenant


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def platform_admin(client):
    r = client.post("/auth/login", json={"email": "admin@platform.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def demo_tenant_id():
    with Session(engine) as s:
        return s.exec(select(Tenant).where(Tenant.slug == "demo-bank")).first().id


def _seed(**kwargs):
    kwargs.setdefault("actor", "x")
    kwargs.setdefault("action", "x")
    with Session(engine) as s:
        s.add(AuditEvent(**kwargs))
        s.commit()


def test_tenant_admin_sees_watchdog_auto_heal_events(client, tadmin):
    _seed(actor="watchdog", action="auto_heal", target="rdb-s0", outcome="success", tenant_id=None)
    r = client.get("/audit?action=auto_heal", headers=tadmin)
    assert r.status_code == 200, r.text
    assert any(e["actor"] == "watchdog" and e["target"] == "rdb-s0" for e in r.json())


def test_tenant_admin_sees_tickerplant_reported_events(client, tadmin):
    _seed(actor="tp:s0", action="slow_sub_discard", target="handle 7", tenant_id=None)
    r = client.get("/audit?action=slow_sub_discard", headers=tadmin)
    assert r.status_code == 200, r.text
    assert any(e["actor"] == "tp:s0" for e in r.json())


def test_tenant_admin_never_sees_another_tenants_create_tenant_event(client, tadmin):
    # create_tenant/suspend_tenant leave tenant_id unset too (they're ABOUT
    # a tenant being created, not scoped to the caller), but the actor is a
    # human platform-admin email, not "watchdog"/"tp:*" - must stay hidden.
    _seed(actor="admin@platform.local", action="create_tenant", target="some-other-bank",
         detail="admin=other-admin@example.com", tenant_id=None)
    r = client.get("/audit?action=create_tenant", headers=tadmin)
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_tenant_admin_never_sees_another_tenants_own_events(client, tadmin):
    _seed(actor="someone@other-bank.local", action="order_placed", target="AAPL", tenant_id=99999)
    r = client.get("/audit?action=order_placed", headers=tadmin)
    assert r.status_code == 200, r.text
    assert all(e["tenant_id"] != 99999 for e in r.json())


def test_tenant_admin_sees_own_tenant_events(client, tadmin, demo_tenant_id):
    _seed(actor="me@demo-bank.local", action="order_placed", target="MSFT", tenant_id=demo_tenant_id)
    r = client.get("/audit?action=order_placed", headers=tadmin)
    assert r.status_code == 200, r.text
    assert any(e["tenant_id"] == demo_tenant_id for e in r.json())


def test_platform_admin_sees_everything_including_create_tenant(client, platform_admin):
    _seed(actor="admin@platform.local", action="create_tenant", target="yet-another-bank", tenant_id=None)
    r = client.get("/audit?action=create_tenant", headers=platform_admin)
    assert r.status_code == 200, r.text
    assert any(e["target"] == "yet-another-bank" for e in r.json())


def test_audit_internal_rejects_bad_secret(client):
    r = client.post("/audit/internal", json={"actor": "watchdog", "action": "auto_heal"},
                    headers={"X-Internal-Secret": "wrong"})
    assert r.status_code == 401


def test_audit_internal_accepts_correct_secret_and_persists(client, tadmin):
    r = client.post("/audit/internal",
                    json={"actor": "watchdog", "action": "detect_failure", "target": "wdb-s1",
                          "detail": "status=exited", "outcome": "detected"},
                    headers={"X-Internal-Secret": settings.watchdog_shared_secret})
    assert r.status_code == 200, r.text
    r2 = client.get("/audit?action=detect_failure", headers=tadmin)
    assert any(e["target"] == "wdb-s1" for e in r2.json())
