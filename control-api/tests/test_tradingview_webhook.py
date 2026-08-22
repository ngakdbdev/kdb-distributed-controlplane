"""Tests for routers/tradingview_webhook.py - the authenticated config API
(/tradingview/*) and the unauthenticated inbound alert endpoint
(/webhooks/tradingview/{token}). Same real-app-and-DB pattern as
test_bot_router.py, with the risk feed and trade-tape faked the same way."""
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import risk_check
from app.routers import tradingview_webhook as tv_router
from app.routers import trading as trading_router


@pytest.fixture(autouse=True)
def no_real_risk_feed(monkeypatch):
    monkeypatch.setattr(trading_router.risk_check, "check_pretrade",
                        lambda symbol: risk_check.CheckResult(block_reason=None))


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _fake_tape(price):
    return lambda symbols: {s: [{"time": "2026-01-01T00:00:00Z", "price": price}] for s in symbols}


# ---- config CRUD -----------------------------------------------------------

def test_get_config_creates_defaults_with_a_real_token(client, tadmin):
    r = client.get("/tradingview/config", headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False
    assert body["allowed_symbols"] == []
    assert len(body["token"]) > 20  # secrets.token_urlsafe(32) - a real random secret, not a placeholder


def test_put_config_updates_allowlist_and_max_qty(client, tadmin):
    r = client.put("/tradingview/config", headers=tadmin,
                   json={"allowed_symbols": ["aapl", "msft"], "max_qty": 50})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allowed_symbols"] == ["AAPL", "MSFT"]  # upper-cased
    assert body["max_qty"] == 50


def test_max_qty_is_hard_capped_server_side(client, tadmin):
    r = client.put("/tradingview/config", headers=tadmin, json={"max_qty": 10_000_000})
    assert r.status_code == 200, r.text
    assert r.json()["max_qty"] == tv_router.MAX_QTY_CAP


def test_enable_requires_a_symbol_in_the_allowlist(client, tadmin):
    r = client.put("/tradingview/config", headers=tadmin, json={"allowed_symbols": [], "enabled": True})
    assert r.status_code == 400, r.text
    assert "allowlist" in r.json()["detail"]


def test_enable_succeeds_once_allowlist_is_set(client, tadmin):
    client.put("/tradingview/config", headers=tadmin, json={"allowed_symbols": ["AAPL"]})
    r = client.put("/tradingview/config", headers=tadmin, json={"enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True


def test_rotate_issues_a_new_token(client, tadmin):
    old_token = client.get("/tradingview/config", headers=tadmin).json()["token"]
    r = client.post("/tradingview/rotate", headers=tadmin)
    assert r.status_code == 200, r.text
    new_token = r.json()["token"]
    assert new_token != old_token


# ---- inbound webhook --------------------------------------------------------

def test_webhook_rejects_unknown_token(client):
    r = client.post("/webhooks/tradingview/not-a-real-token",
                    content='{"symbol": "AAPL", "side": "buy", "qty": 1}')
    assert r.status_code == 404


def test_webhook_rejects_when_disabled(client, tadmin):
    # explicitly disabled rather than relying on the row's default state -
    # this config is a per-tenant singleton (like BotConfig), so an earlier
    # test in this same run may have already enabled it for this tenant.
    client.put("/tradingview/config", headers=tadmin, json={"enabled": False})
    token = client.get("/tradingview/config", headers=tadmin).json()["token"]
    r = client.post(f"/webhooks/tradingview/{token}",
                    content='{"symbol": "AAPL", "side": "buy", "qty": 1}')
    assert r.status_code == 403
    assert "disabled" in r.json()["detail"]


def test_webhook_rejects_symbol_not_in_allowlist(client, tadmin, monkeypatch):
    client.put("/tradingview/config", headers=tadmin, json={"allowed_symbols": ["AAPL"], "enabled": True})
    token = client.get("/tradingview/config", headers=tadmin).json()["token"]
    monkeypatch.setattr(tv_router.signal_engine, "fetch_trade_tape", _fake_tape(150.0))

    r = client.post(f"/webhooks/tradingview/{token}",
                    content='{"symbol": "TSLA", "side": "buy", "qty": 1}')
    assert r.status_code == 403
    assert "not on this webhook's allowed_symbols list" in r.json()["detail"]


def test_webhook_rejects_malformed_json(client, tadmin):
    client.put("/tradingview/config", headers=tadmin, json={"allowed_symbols": ["AAPL"], "enabled": True})
    token = client.get("/tradingview/config", headers=tadmin).json()["token"]

    r = client.post(f"/webhooks/tradingview/{token}", content="not json at all")
    assert r.status_code == 400
    assert "JSON" in r.json()["detail"]


def test_webhook_rejects_missing_recent_price(client, tadmin, monkeypatch):
    client.put("/tradingview/config", headers=tadmin, json={"allowed_symbols": ["ZNOPRICE"], "enabled": True})
    token = client.get("/tradingview/config", headers=tadmin).json()["token"]
    monkeypatch.setattr(tv_router.signal_engine, "fetch_trade_tape", lambda symbols: {s: [] for s in symbols})

    r = client.post(f"/webhooks/tradingview/{token}",
                    content='{"symbol": "ZNOPRICE", "side": "buy", "qty": 1}')
    assert r.status_code == 422
    assert "no recent trade price" in r.json()["detail"]


def test_webhook_places_a_real_order_through_the_same_path_as_a_manual_order(client, tadmin, monkeypatch):
    client.put("/tradingview/config", headers=tadmin,
              json={"allowed_symbols": ["ZTVHOOK"], "enabled": True, "max_qty": 10})
    token = client.get("/tradingview/config", headers=tadmin).json()["token"]
    monkeypatch.setattr(tv_router.signal_engine, "fetch_trade_tape", _fake_tape(42.0))

    r = client.post(f"/webhooks/tradingview/{token}",
                    content='{"symbol": "ZTVHOOK", "side": "buy", "qty": 5}')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] is True
    assert "42.0" in body["detail"]

    orders = client.get("/trading/orders", headers=tadmin).json()
    hook_orders = [o for o in orders if o["symbol"] == "ZTVHOOK"]
    assert len(hook_orders) == 1
    assert hook_orders[0]["qty"] == 5 and hook_orders[0]["fill_price"] == 42.0

    config = client.get("/tradingview/config", headers=tadmin).json()
    assert config["last_triggered_at"] is not None


def test_webhook_qty_is_capped_at_configured_max_qty(client, tadmin, monkeypatch):
    client.put("/tradingview/config", headers=tadmin,
              json={"allowed_symbols": ["ZTVCAP"], "enabled": True, "max_qty": 3})
    token = client.get("/tradingview/config", headers=tadmin).json()["token"]
    monkeypatch.setattr(tv_router.signal_engine, "fetch_trade_tape", _fake_tape(10.0))

    r = client.post(f"/webhooks/tradingview/{token}",
                    content='{"symbol": "ZTVCAP", "side": "buy", "qty": 999}')
    assert r.status_code == 200, r.text

    orders = client.get("/trading/orders", headers=tadmin).json()
    hook_orders = [o for o in orders if o["symbol"] == "ZTVCAP"]
    assert hook_orders[0]["qty"] == 3  # clamped, not the 999 the payload asked for


def test_webhook_defaults_qty_to_max_qty_when_payload_omits_it(client, tadmin, monkeypatch):
    client.put("/tradingview/config", headers=tadmin,
              json={"allowed_symbols": ["ZTVDEF"], "enabled": True, "max_qty": 2})
    token = client.get("/tradingview/config", headers=tadmin).json()["token"]
    monkeypatch.setattr(tv_router.signal_engine, "fetch_trade_tape", _fake_tape(10.0))

    r = client.post(f"/webhooks/tradingview/{token}", content='{"symbol": "ZTVDEF", "side": "sell"}')
    assert r.status_code == 200, r.text

    orders = client.get("/trading/orders", headers=tadmin).json()
    hook_orders = [o for o in orders if o["symbol"] == "ZTVDEF"]
    assert hook_orders[0]["qty"] == 2
