"""Tests for connector symbol-group scoping (GET /connectors symbols field,
PUT /connectors/{id}/symbols)."""
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


def _bpipe_connector(client, tadmin):
    connectors = client.get("/connectors", headers=tadmin).json()
    return next(c for c in connectors if c["service_name"] == "bpipe-sim")


def test_real_provider_feeds_are_registered_connectors(client, tadmin):
    connectors = {c["service_name"]: c for c in client.get("/connectors", headers=tadmin).json()}
    for svc in ("finnhub-feed", "twelvedata-feed", "coinbase-feed", "kraken-feed",
               "binance-feed", "bybit-feed", "okx-feed"):
        assert svc in connectors, f"{svc} not registered as a Connector"
        assert connectors[svc]["enabled"] is False   # opt-in default, same as bpipe-sim/crims-sim
    assert connectors["coinbase-feed"]["kind"] == "crypto"
    assert connectors["finnhub-feed"]["kind"] == "equities"


def test_list_connectors_defaults_to_empty_symbol_group(client, tadmin):
    c = _bpipe_connector(client, tadmin)
    assert c["symbols"] == []   # unscoped == full built-in universe, unchanged default


def test_set_symbols_persists_and_normalizes(client, tadmin):
    c = _bpipe_connector(client, tadmin)
    r = client.put(f"/connectors/{c['id']}/symbols",
                   json={"symbols": ["aapl", "msft", "AAPL", "  "]}, headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbols"] == ["AAPL", "MSFT"]   # upper-cased, deduped, blanks dropped

    refreshed = _bpipe_connector(client, tadmin)
    assert refreshed["symbols"] == ["AAPL", "MSFT"]

    # clearing it back to [] restores the "full universe" default
    r2 = client.put(f"/connectors/{c['id']}/symbols", json={"symbols": []}, headers=tadmin)
    assert r2.json()["symbols"] == []


def test_set_symbols_reports_live_status_without_crashing_when_disabled(client, tadmin):
    c = _bpipe_connector(client, tadmin)
    assert c["enabled"] is False   # sims start disabled by default (see db.py seeding)
    r = client.put(f"/connectors/{c['id']}/symbols", json={"symbols": ["AAPL"]}, headers=tadmin)
    assert r.status_code == 200, r.text
    # not enabled -> no live container recreate attempted
    assert r.json()["live_applied"] is False


def test_set_symbols_404s_for_unknown_connector(client, tadmin):
    r = client.put("/connectors/999999/symbols", json={"symbols": ["AAPL"]}, headers=tadmin)
    assert r.status_code == 404
