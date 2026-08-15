"""Tests for periodic metrics-snapshot capture (app/metrics_history.py) and
the trend history it feeds (routers/metrics.py's /metrics/history)."""
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import metrics_history


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


def test_capture_once_persists_a_snapshot_visible_via_history(client, monkeypatch):
    monkeypatch.setattr("app.metrics_history.orchestrator.status_all",
                        lambda: {"tp-s0": "running", "rdb-s0": "running", "hdb-s0": "exited"})
    monkeypatch.setattr("app.metrics_history.gateway_client.row_counts",
                        lambda: {"trade": 1234, "risk": 56})
    monkeypatch.setattr("app.metrics_history.gateway_client.health",
                        lambda: [{"shard": "s0", "rdb": {"status": "up"}}])

    snap = metrics_history.capture_once()
    assert snap.containers_running == 2 and snap.containers_total == 3
    assert snap.rows_trade == 1234 and snap.rows_risk == 56
    assert snap.shards_healthy == 1 and snap.shards_total == 1

    r = client.get("/metrics/history?hours=1")
    assert r.status_code == 200
    rows = r.json()
    assert any(row["rows_trade"] == 1234 for row in rows)


def test_capture_survives_a_failing_field(client, monkeypatch):
    monkeypatch.setattr("app.metrics_history.orchestrator.status_all",
                        lambda: (_ for _ in ()).throw(RuntimeError("orchestrator down")))
    monkeypatch.setattr("app.metrics_history.gateway_client.row_counts",
                        lambda: {"trade": 5, "risk": 0})
    monkeypatch.setattr("app.metrics_history.gateway_client.health", lambda: [])
    snap = metrics_history.capture_once()
    assert snap.containers_running == 0 and snap.containers_total == 0
    assert snap.rows_trade == 5


def test_history_endpoint_shape(client):
    r = client.get("/metrics/history")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
