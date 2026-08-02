"""Tests for the trading terminal: greeks, portfolio, market/forecast, OMS, API."""
import math

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import greeks as gk
from app import portfolio as pf
from app import market as mkt
from app import oms


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
