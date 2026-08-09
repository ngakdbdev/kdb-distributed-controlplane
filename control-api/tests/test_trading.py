"""Tests for the trading terminal: greeks, portfolio, market/forecast, OMS, API."""
import math

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import greeks as gk
from app import portfolio as pf
from app import market as mkt
from app import oms
from app import risk_check
from app.routers import trading as trading_router

# captured before the autouse fixture below can patch it, so the direct
# risk_check unit tests (which want the REAL function, not the test-friendly
# stub) can restore it for their own duration.
_REAL_CHECK_PRETRADE = risk_check.check_pretrade


@pytest.fixture(autouse=True)
def no_real_risk_feed(monkeypatch):
    """The endpoint tests below run against the real app/DB but there's no
    live gateway/risk feed reachable in the test sandbox - and even where
    there is one (e.g. run inside the deployed container), a real BREACH row
    for a test symbol would make these flaky. Default every test to "not
    blocked"; individual tests override this to exercise the block path."""
    monkeypatch.setattr(trading_router.risk_check, "check_pretrade", lambda symbol: None)


# ---- greeks (Black-Scholes) ----------------------------------------------

def test_atm_call_delta_near_half():
    g = gk.greeks(spot=100, strike=100, t_years=1.0, vol=0.2, rate=0.0, kind="call")
    assert 0.5 < g["delta"] < 0.6           # ATM call slightly above 0.5
    assert g["gamma"] > 0 and g["vega"] > 0

def test_put_call_parity():
    c = gk.greeks(100, 100, 1.0, 0.2, rate=0.05, kind="call")["price"]
    p = gk.greeks(100, 100, 1.0, 0.2, rate=0.05, kind="put")["price"]
    # C - P == S - K e^{-rT}
    assert abs((c - p) - (100 - 100 * math.exp(-0.05))) < 1e-6

def test_call_and_put_delta_signs():
    assert gk.greeks(100, 100, 0.5, 0.25, kind="call")["delta"] > 0
    assert gk.greeks(100, 100, 0.5, 0.25, kind="put")["delta"] < 0

def test_greeks_reject_bad_input():
    with pytest.raises(ValueError):
        gk.greeks(0, 100, 1, 0.2)

def test_implied_vol_roundtrips():
    price = gk.greeks(100, 105, 0.5, 0.3, rate=0.02, kind="call")["price"]
    iv = gk.implied_vol(price, 100, 105, 0.5, rate=0.02, kind="call")
    assert abs(iv - 0.3) < 1e-3


# ---- portfolio ------------------------------------------------------------

def test_portfolio_pnl_and_exposure():
    positions = [{"symbol": "AAPL", "qty": 100, "avg_price": 150},
                 {"symbol": "TSLA", "qty": -10, "avg_price": 250}]  # short
    prices = {"AAPL": 160, "TSLA": 240}
    s = pf.portfolio_summary(positions, prices)
    aapl = next(p for p in s["positions"] if p["symbol"] == "AAPL")
    assert aapl["unrealized_pnl"] == pytest.approx(1000)      # (160-150)*100
    assert s["short_exposure"] == pytest.approx(-2400)        # -10*240
    assert s["gross_exposure"] == pytest.approx(16000 + 2400)


# ---- market summary + forecast -------------------------------------------

def test_market_summary():
    s = mkt.summarize([100, 101, 102, 101, 103], [10, 20, 30, 10, 30])
    assert s["last"] == 103 and s["high"] == 103 and s["low"] == 100
    assert s["change"] == 3 and s["vwap"] is not None

def test_forecast_has_bands_and_disclaimer():
    f = mkt.forecast([100, 101, 102, 103, 104, 105], horizon=5)
    assert len(f["points"]) == 5
    assert f["points"][0]["lower"] < f["points"][0]["expected"] < f["points"][0]["upper"]
    assert "not investment advice" in f["disclaimer"].lower() or "not a prediction" in f["disclaimer"].lower()
    assert f["trend"] == "up"

def test_forecast_insufficient_data():
    assert mkt.forecast([100]) ["points"] == []


# ---- OMS position math ----------------------------------------------------

def test_position_open_and_add_weighted_avg():
    q, a, r = oms.apply_to_position(0, 0, "buy", 100, 10)
    assert (q, a, r) == (100, 10, 0)
    q, a, r = oms.apply_to_position(100, 10, "buy", 100, 12)
    assert q == 200 and a == pytest.approx(11) and r == 0

def test_position_reduce_books_realized_pnl():
    q, a, r = oms.apply_to_position(100, 10, "sell", 40, 15)
    assert q == 60 and a == 10 and r == pytest.approx(40 * (15 - 10))

def test_position_flip_through_zero():
    q, a, r = oms.apply_to_position(50, 10, "sell", 80, 20)
    assert q == -30 and a == 20                      # remainder at fill price
    assert r == pytest.approx(50 * (20 - 10))        # realized on the 50 closed

def test_paper_router_needs_ref_price_for_market():
    with pytest.raises(oms.OrderError):
        oms.PaperRouter().fill("buy", 10, "market", None, None)

def test_broker_router_refuses():
    with pytest.raises(oms.OrderRoutingNotConfigured):
        oms.BrokerRouter().fill("buy", 10, "market", 100, None)

def test_crosses_buy_limit():
    assert oms.crosses("buy", limit_price=100, market_price=99)    # market at/below -> crosses
    assert oms.crosses("buy", limit_price=100, market_price=100)
    assert not oms.crosses("buy", limit_price=100, market_price=101)

def test_crosses_sell_limit():
    assert oms.crosses("sell", limit_price=100, market_price=101)  # market at/above -> crosses
    assert not oms.crosses("sell", limit_price=100, market_price=99)


# ---- pre-trade risk check --------------------------------------------------

def test_risk_check_passes_when_no_breach(monkeypatch):
    monkeypatch.setattr(risk_check, "check_pretrade", _REAL_CHECK_PRETRADE)
    # run_query needs a real conn shape; patch it directly for this pure unit test
    monkeypatch.setattr(risk_check.qs, "run_query", lambda q, conn, limit: {
        "columns": ["time", "sym", "riskType", "limit", "exposure", "status", "shard"],
        "rows": [[1, "AAPL", "VAR", 1000000, 500000, "OK", "s0"]],
    })
    assert risk_check.check_pretrade("AAPL", connect=lambda: object()) is None

def test_risk_check_blocks_on_breach(monkeypatch):
    monkeypatch.setattr(risk_check, "check_pretrade", _REAL_CHECK_PRETRADE)
    monkeypatch.setattr(risk_check.qs, "run_query", lambda q, conn, limit: {
        "columns": ["time", "sym", "riskType", "limit", "exposure", "status", "shard"],
        "rows": [[1, "AAPL", "VAR", 1000000, 1200000, "BREACH", "s0"]],
    })
    reason = risk_check.check_pretrade("AAPL", connect=lambda: object())
    assert reason and "BREACH" in reason

def test_risk_check_fails_open_when_feed_unreachable(monkeypatch):
    monkeypatch.setattr(risk_check, "check_pretrade", _REAL_CHECK_PRETRADE)
    def _boom():
        raise ConnectionRefusedError("no gateway")
    assert risk_check.check_pretrade("AAPL", connect=_boom) is None


# ---- endpoints ------------------------------------------------------------

@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_admin_has_trading_permission(client, tadmin):
    r = client.get("/trading/permission", headers=tadmin)
    assert r.json()["can_trade"] is True and r.json()["mode"] == "paper"


def test_place_order_paper_fills_and_moves_position(client, tadmin):
    r = client.post("/trading/orders", headers=tadmin,
                    json={"symbol": "AAPL", "side": "buy", "qty": 100,
                          "order_type": "market", "ref_price": 150.0})
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["status"] == "filled" and o["route"] == "paper" and o["fill_price"] == 150.0

    pos = client.get("/trading/positions?marks=AAPL:160", headers=tadmin).json()
    aapl = next(p for p in pos["positions"] if p["symbol"] == "AAPL")
    assert aapl["qty"] == 100 and aapl["unrealized_pnl"] == pytest.approx(1000)


def test_market_order_without_ref_price_rejected(client, tadmin):
    r = client.post("/trading/orders", headers=tadmin,
                    json={"symbol": "AAPL", "side": "buy", "qty": 10, "order_type": "market"})
    assert r.status_code == 400


def test_non_marketable_limit_order_rests_and_is_cancellable(client, tadmin):
    # buy limit well below the market -> doesn't cross, so it should rest
    r = client.post("/trading/orders", headers=tadmin,
                    json={"symbol": "MSFT", "side": "buy", "qty": 50, "order_type": "limit",
                          "limit_price": 100.0, "ref_price": 300.0})
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["status"] == "new" and o["fill_price"] is None

    # resting, so cancel is now reachable (this was dead code before the fix)
    c = client.post(f"/trading/orders/{o['id']}/cancel", headers=tadmin)
    assert c.status_code == 200 and c.json()["status"] == "cancelled"

    # cancelling twice is rejected - it's no longer "new"
    c2 = client.post(f"/trading/orders/{o['id']}/cancel", headers=tadmin)
    assert c2.status_code == 400


def test_marketable_limit_order_fills_immediately(client, tadmin):
    # sell limit at/below the market -> crosses immediately
    r = client.post("/trading/orders", headers=tadmin,
                    json={"symbol": "MSFT", "side": "sell", "qty": 10, "order_type": "limit",
                          "limit_price": 290.0, "ref_price": 300.0})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "filled" and r.json()["fill_price"] == 290.0


def test_match_endpoint_fills_a_crossed_resting_order(client, tadmin):
    r = client.post("/trading/orders", headers=tadmin,
                    json={"symbol": "NVDA", "side": "buy", "qty": 20, "order_type": "limit",
                          "limit_price": 100.0, "ref_price": 150.0})
    order_id = r.json()["id"]
    assert r.json()["status"] == "new"

    # market hasn't moved yet - no fill
    m1 = client.post("/trading/orders/match", headers=tadmin,
                     json={"symbol": "NVDA", "price": 150.0})
    assert m1.json()["filled"] == []

    # market drops through the limit - now it crosses
    m2 = client.post("/trading/orders/match", headers=tadmin,
                     json={"symbol": "NVDA", "price": 99.0})
    filled_ids = [o["id"] for o in m2.json()["filled"]]
    assert order_id in filled_ids

    orders = {o["id"]: o for o in client.get("/trading/orders", headers=tadmin).json()}
    assert orders[order_id]["status"] == "filled" and orders[order_id]["fill_price"] == 100.0

    pos = client.get("/trading/positions?marks=NVDA:110", headers=tadmin).json()
    nvda = next(p for p in pos["positions"] if p["symbol"] == "NVDA")
    assert nvda["qty"] == 20


def test_order_blocked_by_pretrade_risk_breach(client, tadmin, monkeypatch):
    monkeypatch.setattr(trading_router.risk_check, "check_pretrade",
                        lambda symbol: f"pre-trade risk check failed: {symbol} is in BREACH")
    r = client.post("/trading/orders", headers=tadmin,
                    json={"symbol": "TSLA", "side": "buy", "qty": 10,
                          "order_type": "market", "ref_price": 200.0})
    assert r.status_code == 400
    assert "BREACH" in r.json()["detail"]


def test_greeks_endpoint(client, tadmin):
    r = client.post("/trading/greeks", headers=tadmin,
                    json={"spot": 100, "strike": 100, "t_years": 1, "vol": 0.2, "kind": "call"})
    assert r.status_code == 200 and 0.5 < r.json()["delta"] < 0.6


def test_forecast_endpoint_carries_disclaimer(client, tadmin):
    r = client.post("/trading/forecast", headers=tadmin,
                    json={"prices": [100, 101, 102, 103, 104], "horizon": 3})
    assert r.status_code == 200 and r.json()["disclaimer"]


def test_trading_requires_auth(client):
    assert client.get("/trading/permission").status_code in (401, 403)
