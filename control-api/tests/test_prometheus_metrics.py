"""Tests for the real Prometheus exposition-format endpoint (app/
prometheus_metrics.py, GET /metrics) - every value it renders comes from
the same live data routers/metrics.py's own _snapshot() and the Order/
AuditEvent tables already serve to the JSON dashboard endpoints, so these
tests monkeypatch the exact same seams test_metrics_history.py does."""
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import risk_check


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _patch_snapshot(monkeypatch, *, services=None, component_metrics=None, row_counts=None):
    monkeypatch.setattr("app.routers.metrics.orchestrator.status_all", lambda: services or {})
    monkeypatch.setattr("app.routers.metrics.gateway_client.component_metrics", lambda: component_metrics or [])
    monkeypatch.setattr("app.routers.metrics.gateway_client.row_counts", lambda: row_counts or {"trade": 0, "risk": 0})
    monkeypatch.setattr("app.routers.metrics.gateway_client.health", lambda: [])
    monkeypatch.setattr("app.routers.metrics.gateway_client.transit_lag", lambda: [])


def test_metrics_is_unauthenticated_like_snapshot(client, monkeypatch):
    _patch_snapshot(monkeypatch)
    r = client.get("/metrics")
    assert r.status_code == 200


def test_metrics_content_type_is_prometheus_exposition_format(client, monkeypatch):
    _patch_snapshot(monkeypatch)
    r = client.get("/metrics")
    assert "text/plain" in r.headers["content-type"]


def test_service_up_reflects_real_orchestrator_status(client, monkeypatch):
    _patch_snapshot(monkeypatch, services={"tp-s0": "running", "hdb-s0": "exited"})
    body = client.get("/metrics").text
    assert 'vantik_service_up{service="tp-s0"} 1.0' in body
    assert 'vantik_service_up{service="hdb-s0"} 0.0' in body


def test_component_metrics_render_per_shard(client, monkeypatch):
    _patch_snapshot(monkeypatch, component_metrics=[
        {"shard": "s0", "tpRecv": 42318, "tpPub": 42300, "tpQueue": 0, "tpSubLag": 0,
         "tpPubLatencyUs": 3, "tpLogLatencyUs": 5, "rdbRowsTrade": 100, "rdbRowsRisk": 10,
         "rdbReconnects": 0, "rdbConnected": True, "wdbConnected": True, "wdbReconnects": 0},
    ])
    body = client.get("/metrics").text
    assert 'vantik_tp_recv_total{shard="s0"} 42318.0' in body
    assert 'vantik_tp_publish_latency_us{shard="s0"} 3.0' in body
    assert 'vantik_rdb_connected{shard="s0"} 1.0' in body


def test_component_metrics_tolerates_missing_fields(client, monkeypatch):
    # real componentMetrics rows can arrive with nulls (see kdb_client.py's
    # own column-padding for short kdb rows) - a None must not 500 the
    # whole scrape, just skip that one series.
    _patch_snapshot(monkeypatch, component_metrics=[{"shard": "s0", "tpRecv": None}])
    r = client.get("/metrics")
    assert r.status_code == 200
    assert 'vantik_tp_recv_total{shard="s0"}' not in r.text


def test_row_counts_render(client, monkeypatch):
    _patch_snapshot(monkeypatch, row_counts={"trade": 1234, "risk": 56})
    body = client.get("/metrics").text
    assert 'vantik_row_count{table="trade"} 1234.0' in body
    assert 'vantik_row_count{table="risk"} 56.0' in body


def test_orders_total_grouped_by_real_status(client, tadmin, monkeypatch):
    _patch_snapshot(monkeypatch)
    # Real pretrade risk check fails closed when it can't reach a risk
    # feed (no gateway in this test process) - same override
    # test_trading.py's own risk-gate tests use, not a special-case for
    # this test.
    monkeypatch.setattr(risk_check._settings, "risk_gate_fail_open", True)
    # Place one order through the real API so it lands in the real table -
    # simplest way to get a genuine, non-mocked Order row for this test.
    r = client.post("/trading/orders", json={
        "symbol": "ZZTEST-PROM", "side": "buy", "qty": 1, "order_type": "market", "ref_price": 100.0,
    }, headers=tadmin)
    assert r.status_code == 200, r.text

    body = client.get("/metrics").text
    assert "vantik_orders_total{status=" in body


def test_audit_events_total_grouped_by_actor_and_outcome(client, tadmin, monkeypatch):
    _patch_snapshot(monkeypatch)
    # Any admin action logs a real AuditEvent (see log_event() call sites) -
    # updating a feed handler is a simple, side-effect-light way to trigger one.
    client.post("/feedhandlers", json={"provider": "COINBASE", "feed": "MATCHES"}, headers=tadmin)

    body = client.get("/metrics").text
    assert "vantik_audit_events_total{actor=" in body
