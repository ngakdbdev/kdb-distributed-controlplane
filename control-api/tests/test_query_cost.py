"""Tests for query_cost.py - per-tenant query budget tracking/enforcement/
showback. Ties together query_service's row-cap and query_advisor's
scan-risk tip into an aggregate-over-time governance layer."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

import app.main as m
from app import query_cost
from app.models import QueryCostEvent


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_consumed_ms_sums_only_this_tenant_in_window(session):
    session.add(QueryCostEvent(tenant_id=1, actor="a@x.com", elapsed_ms=100))
    session.add(QueryCostEvent(tenant_id=1, actor="a@x.com", elapsed_ms=250))
    session.add(QueryCostEvent(tenant_id=2, actor="b@x.com", elapsed_ms=9999))  # other tenant
    session.commit()
    assert query_cost.consumed_ms(session, tenant_id=1, window_hours=1) == 350
    assert query_cost.consumed_ms(session, tenant_id=2, window_hours=1) == 9999
    assert query_cost.consumed_ms(session, tenant_id=3, window_hours=1) == 0  # no activity

def test_consumed_ms_excludes_events_outside_the_window(session):
    from datetime import datetime, timedelta
    old = QueryCostEvent(tenant_id=1, actor="a@x.com", elapsed_ms=500,
                         timestamp=datetime.utcnow() - timedelta(hours=5))
    recent = QueryCostEvent(tenant_id=1, actor="a@x.com", elapsed_ms=100)
    session.add(old); session.add(recent); session.commit()
    assert query_cost.consumed_ms(session, tenant_id=1, window_hours=1) == 100

def test_check_budget_noop_when_disabled(session, monkeypatch):
    monkeypatch.setattr(query_cost.settings, "query_budget_ms_per_window", 0)
    session.add(QueryCostEvent(tenant_id=1, actor="a@x.com", elapsed_ms=1_000_000))
    session.commit()
    assert query_cost.check_budget(session, tenant_id=1) is None

def test_check_budget_blocks_once_exceeded(session, monkeypatch):
    monkeypatch.setattr(query_cost.settings, "query_budget_ms_per_window", 1000)
    monkeypatch.setattr(query_cost.settings, "query_budget_window_hours", 1)
    session.add(QueryCostEvent(tenant_id=1, actor="a@x.com", elapsed_ms=1200))
    session.commit()
    reason = query_cost.check_budget(session, tenant_id=1)
    assert reason and "budget exceeded" in reason

def test_check_budget_allows_under_budget(session, monkeypatch):
    monkeypatch.setattr(query_cost.settings, "query_budget_ms_per_window", 1000)
    session.add(QueryCostEvent(tenant_id=1, actor="a@x.com", elapsed_ms=200))
    session.commit()
    assert query_cost.check_budget(session, tenant_id=1) is None

def test_check_budget_fails_open_on_db_error(session, monkeypatch):
    monkeypatch.setattr(query_cost.settings, "query_budget_ms_per_window", 1000)
    def _boom(*a, **kw):
        raise RuntimeError("db unavailable")
    monkeypatch.setattr(query_cost, "consumed_ms", _boom)
    assert query_cost.check_budget(session, tenant_id=1) is None

def test_tenant_summary_shape_when_disabled(session):
    result = query_cost.tenant_summary(session, tenant_id=1)
    assert result["enforced"] is False
    assert result["budget_ms"] is None and result["remaining_ms"] is None

def test_tenant_summary_shape_when_enforced(session, monkeypatch):
    monkeypatch.setattr(query_cost.settings, "query_budget_ms_per_window", 1000)
    session.add(QueryCostEvent(tenant_id=1, actor="a@x.com", elapsed_ms=300))
    session.commit()
    result = query_cost.tenant_summary(session, tenant_id=1)
    assert result["enforced"] is True
    assert result["consumed_ms"] == 300 and result["remaining_ms"] == 700
    assert result["percent_used"] == 30.0

def test_all_tenants_summary_sorted_by_consumption(session):
    session.add(QueryCostEvent(tenant_id=1, actor="a@x.com", elapsed_ms=50))
    session.add(QueryCostEvent(tenant_id=2, actor="b@x.com", elapsed_ms=500))
    session.commit()
    rows = query_cost.all_tenants_summary(session)
    assert [r["tenant_id"] for r in rows] == [2, 1]
    assert rows[0]["consumed_ms"] == 500 and rows[0]["query_count"] == 1


# ---- endpoint-level: budget enforcement + platform-admin exemption -------

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


def test_run_blocked_with_429_once_tenant_budget_exhausted(client, tadmin, monkeypatch):
    from app.routers import query as query_router
    monkeypatch.setattr(query_router.query_cost.settings, "query_budget_ms_per_window", 1)
    monkeypatch.setattr(query_router.query_cost, "check_budget",
                        lambda session, tenant_id: "query budget exceeded: fake for test")
    r = client.post("/query/run", json={"target": "gateway", "query": "tables[]"}, headers=tadmin)
    assert r.status_code == 429
    assert "budget" in r.json()["detail"]

def test_cost_summary_endpoint_for_tenant_user(client, tadmin):
    r = client.get("/query/cost/summary", headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enforced"] is False   # disabled by default in tests
    assert "tenant_id" in body

def test_cost_summary_endpoint_for_platform_admin_lists_tenants(client, platform_admin):
    r = client.get("/query/cost/summary", headers=platform_admin)
    assert r.status_code == 200, r.text
    assert "tenants" in r.json()
