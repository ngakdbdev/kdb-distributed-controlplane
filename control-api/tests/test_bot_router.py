"""Endpoint tests for routers/bot.py - the server-side signal bot's
config/positions/log/close API. Same real-app-and-DB pattern as
test_trading.py, with the risk feed faked the same way."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

import app.main as m
from app import risk_check, signal_engine
from app.db import engine
from app.models import BotPosition
from app.routers import bot as bot_router
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


def test_get_config_creates_defaults_on_first_read(client, tadmin):
    r = client.get("/bot/config", headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False and body["mode"] == "manual"
    assert body["symbols"] == [] and body["max_risk_pct"] == signal_engine.MAX_RISK_PCT


def test_put_config_updates_fields(client, tadmin):
    r = client.put("/bot/config", headers=tadmin,
                   json={"mode": "manual", "symbols": ["aapl", "msft"], "paper_capital": 5000,
                        "risk_pct": 0.5, "stop_loss_pct": 2.0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbols"] == ["AAPL", "MSFT"]  # upper-cased
    assert body["paper_capital"] == 5000 and body["risk_pct"] == 0.5


def test_risk_pct_is_hard_capped_server_side(client, tadmin):
    """Even a direct API call (bypassing the web UI's own client-side cap)
    can't set risk_pct above the hard cap - this is the whole point of
    persisting/validating it server-side instead of trusting the browser."""
    r = client.put("/bot/config", headers=tadmin, json={"risk_pct": 50.0})
    assert r.status_code == 200, r.text
    assert r.json()["risk_pct"] == signal_engine.MAX_RISK_PCT


def test_basket_is_capped_at_max_basket(client, tadmin):
    too_many = [f"SY{i}" for i in range(10)]
    r = client.put("/bot/config", headers=tadmin, json={"symbols": too_many})
    assert r.status_code == 200, r.text
    assert len(r.json()["symbols"]) == signal_engine.MAX_BASKET


def test_enable_requires_a_symbol_in_manual_mode(client, tadmin):
    client.put("/bot/config", headers=tadmin, json={"mode": "manual", "symbols": []})
    r = client.put("/bot/config", headers=tadmin, json={"enabled": True})
    assert r.status_code == 400
    assert "symbol" in r.json()["detail"].lower()


def test_enable_succeeds_once_a_symbol_is_configured(client, tadmin):
    client.put("/bot/config", headers=tadmin, json={"mode": "manual", "symbols": ["AAPL"]})
    r = client.put("/bot/config", headers=tadmin, json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True


def test_positions_and_log_start_empty(client, tadmin):
    assert client.get("/bot/positions", headers=tadmin).json() == []
    assert client.get("/bot/log", headers=tadmin).json() == []


def test_close_position_with_no_open_position_404s(client, tadmin):
    r = client.post("/bot/positions/AAPL/close", headers=tadmin)
    assert r.status_code == 404


def test_close_open_position_sells_and_removes_it(client, tadmin, monkeypatch):
    # A synthetic symbol (not AAPL/MSFT/etc) - this test places a REAL order
    # through the shared app DB (same engine test_trading.py's endpoint tests
    # use), so it must not touch a symbol whose Position qty another test
    # file asserts an absolute value for.
    login = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    tenant_id = login.json()["tenant_id"]
    with Session(engine) as s:
        s.add(BotPosition(tenant_id=tenant_id, symbol="ZBOT", qty=10, entry_price=100.0, stop_price=90.0))
        s.commit()

    monkeypatch.setattr(bot_router.signal_engine, "fetch_trade_tape",
                        lambda symbols: {"ZBOT": [{"time": "2026-01-01T00:00:00Z", "price": 105.0}]})

    r = client.post("/bot/positions/ZBOT/close", headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "ZBOT" and body["fill_price"] == 105.0
    assert body["pnl"] == pytest.approx((105.0 - 100.0) * 10)

    assert client.get("/bot/positions", headers=tadmin).json() == []
    log = client.get("/bot/log", headers=tadmin).json()
    assert any("manually closed" in e["reason"] for e in log)


def test_symbol_with_open_position_cannot_be_removed_from_basket(client, tadmin):
    login = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    tenant_id = login.json()["tenant_id"]
    client.put("/bot/config", headers=tadmin, json={"symbols": ["AAPL", "MSFT"]})
    with Session(engine) as s:
        s.add(BotPosition(tenant_id=tenant_id, symbol="AAPL", qty=5, entry_price=100.0, stop_price=90.0))
        s.commit()

    r = client.put("/bot/config", headers=tadmin, json={"symbols": ["MSFT"]})  # drops AAPL
    assert r.status_code == 400
    assert "AAPL" in r.json()["detail"]


def test_config_requires_auth(client):
    assert client.get("/bot/config").status_code in (401, 403)


def test_put_config_requires_trading_permission(client):
    r = client.put("/bot/config", json={"risk_pct": 0.5})
    assert r.status_code in (401, 403)
