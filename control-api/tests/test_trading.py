"""Tests for the trading terminal: greeks, portfolio, market/forecast, OMS, API."""
import math

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import alpaca_broker
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
    monkeypatch.setattr(trading_router.risk_check, "check_pretrade",
                        lambda symbol: risk_check.CheckResult(block_reason=None))


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


class _FakeAlpacaClient:
    """Stands in for alpaca_broker.AlpacaClient - no network, no import of
    the real HTTP plumbing needed to test oms.AlpacaRouter's own logic."""
    def __init__(self, filled_price=180.5, filled_qty=None, raise_on_submit=None, raise_on_wait=None):
        self.filled_price = filled_price
        self.filled_qty = filled_qty
        self.raise_on_submit = raise_on_submit
        self.raise_on_wait = raise_on_wait
        self.submitted = None

    def submit_order(self, **kwargs):
        if self.raise_on_submit:
            raise self.raise_on_submit
        self.submitted = kwargs
        return {"id": "order-1", "status": "accepted"}

    def wait_for_fill(self, order_id):
        if self.raise_on_wait:
            raise self.raise_on_wait
        return {"id": order_id, "status": "filled",
                "filled_avg_price": str(self.filled_price),
                "filled_qty": str(self.filled_qty if self.filled_qty is not None else 10)}


def test_alpaca_router_needs_a_symbol():
    with pytest.raises(oms.OrderError, match="needs a symbol"):
        oms.AlpacaRouter(_FakeAlpacaClient()).fill("buy", 10, "market", 100, None)


def test_alpaca_router_submits_and_waits_then_returns_the_real_fill():
    client = _FakeAlpacaClient(filled_price=180.5, filled_qty=10)
    router = oms.AlpacaRouter(client, live=False)
    fill = router.fill("buy", 10, "market", 100, None, symbol="AAPL")
    assert fill.price == 180.5
    assert fill.qty == 10
    assert fill.route == "alpaca-paper"
    assert client.submitted["symbol"] == "AAPL"
    assert client.submitted["side"] == "buy"


def test_alpaca_router_live_flag_sets_route_name():
    router = oms.AlpacaRouter(_FakeAlpacaClient(), live=True)
    fill = router.fill("buy", 1, "market", 100, None, symbol="AAPL")
    assert fill.route == "alpaca-live"


def test_alpaca_router_wraps_submit_failures_as_order_error():
    client = _FakeAlpacaClient(raise_on_submit=RuntimeError("network down"))
    with pytest.raises(oms.OrderError, match="alpaca order failed"):
        oms.AlpacaRouter(client).fill("buy", 1, "market", 100, None, symbol="AAPL")


def test_alpaca_router_wraps_fill_wait_failures_as_order_error():
    client = _FakeAlpacaClient(raise_on_wait=RuntimeError("order rejected"))
    with pytest.raises(oms.OrderError, match="alpaca order failed"):
        oms.AlpacaRouter(client).fill("buy", 1, "market", 100, None, symbol="AAPL")


# ---- pre-flight tradability check - confirmed live: a symbol this platform's
# own live-discovered universe includes but Alpaca doesn't list (e.g. a
# crypto cross-rate like BTC-GBP) used to reach Alpaca's API and come back a
# raw HTTP 422 with Alpaca's own JSON error text, surfaced verbatim to
# whoever placed the order. fill() now rejects it BEFORE ever calling the
# broker, with a message that actually says what's wrong.

def test_alpaca_router_rejects_a_symbol_the_broker_does_not_list(monkeypatch):
    # ZZTEST-prefixed - see test_symbols.py's own convention: the
    # tradability cache is module-global for the whole pytest run, so a
    # symbol another test file's assertions could also touch is worth
    # avoiding rather than relying on test ordering to stay collision-free.
    monkeypatch.setattr(alpaca_broker, "client_from_env",
                        lambda: type("C", (), {"is_tradable": lambda self, sym: False})())
    client = _FakeAlpacaClient()
    with pytest.raises(oms.OrderError, match="not a tradable asset"):
        oms.AlpacaRouter(client).fill("buy", 1, "market", 100, None, symbol="ZZTEST-GBP")
    assert client.submitted is None  # never reached the broker at all


def test_alpaca_router_proceeds_when_broker_confirms_tradable(monkeypatch):
    monkeypatch.setattr(alpaca_broker, "client_from_env",
                        lambda: type("C", (), {"is_tradable": lambda self, sym: True})())
    client = _FakeAlpacaClient(filled_price=180.5)
    fill = oms.AlpacaRouter(client).fill("buy", 1, "market", 100, None, symbol="ZZTEST-TRADABLE")
    assert fill.price == 180.5
    assert client.submitted is not None


def test_alpaca_router_skips_the_check_when_no_broker_is_configured():
    # the common case in every existing test in this file: no ALPACA_* env
    # vars set -> client_from_env() is None -> nothing to check against,
    # so the pre-flight check must never block an otherwise-fine order.
    client = _FakeAlpacaClient(filled_price=99.0)
    fill = oms.AlpacaRouter(client).fill("buy", 1, "market", 100, None, symbol="ZZTEST-NOBROKER")
    assert fill.price == 99.0


# ---- trading.py's router selection (_router()) - the safe-by-default seam -

def test_router_selection_defaults_to_internal_paper_router_when_unconfigured():
    # no Alpaca env vars set anywhere in this test run (see conftest/env) -
    # must be the exact same PaperRouter instance the module always used,
    # not a new/different one, so nothing about existing behavior changes.
    assert trading_router._router() is trading_router._PAPER


def test_router_selection_switches_to_alpaca_when_configured(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_MODE", "paper")
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    router = trading_router._router()
    assert isinstance(router, oms.AlpacaRouter)
    assert router.route_name == "alpaca-paper"


def test_router_selection_switches_to_ibkr_when_configured(monkeypatch):
    monkeypatch.setenv("IBKR_TRADING_MODE", "paper")
    router = trading_router._router()
    assert isinstance(router, oms.IBKRRouter)
    assert router.route_name == "ibkr-paper"


def test_router_selection_refuses_when_both_brokers_configured(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_MODE", "paper")
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    monkeypatch.setenv("IBKR_TRADING_MODE", "paper")
    with pytest.raises(oms.OrderError, match="both Alpaca and IBKR"):
        trading_router._router()


def test_permission_endpoint_reports_misconfigured_instead_of_500ing(client, tadmin, monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_MODE", "paper")
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    monkeypatch.setenv("IBKR_TRADING_MODE", "paper")
    r = client.get("/trading/permission", headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "misconfigured"
    assert body["live_routing"] is False
    assert "both Alpaca and IBKR" in body["error"]


# ---- oms.IBKRRouter ---------------------------------------------------------

class _FakeIBKRClient:
    def __init__(self, filled_price=180.5, account_id="DU1234567", raise_on_submit=None):
        self.filled_price = filled_price
        self._account_id = account_id
        self.raise_on_submit = raise_on_submit
        self.submitted = None

    def primary_account_id(self):
        return self._account_id

    def resolve_conid(self, symbol):
        return 265598 if symbol == "AAPL" else None

    def submit_order(self, account_id, conid, side, qty, **kwargs):
        if self.raise_on_submit:
            raise self.raise_on_submit
        self.submitted = {"account_id": account_id, "conid": conid, "side": side, "qty": qty, **kwargs}
        return {"order_id": "abc123", "order_status": "Submitted"}

    def wait_for_fill(self, order_id):
        return {"order_id": order_id, "order_status": "Filled", "avg_price": str(self.filled_price)}


def test_ibkr_router_needs_a_symbol():
    with pytest.raises(oms.OrderError, match="needs a symbol"):
        oms.IBKRRouter(_FakeIBKRClient(), mode="paper").fill("buy", 10, "market", 100, None)


def test_ibkr_router_places_and_returns_real_fill():
    client = _FakeIBKRClient(filled_price=180.5)
    router = oms.IBKRRouter(client, mode="paper")
    fill = router.fill("buy", 10, "market", 100, None, symbol="AAPL")
    assert fill.price == 180.5
    assert fill.qty == 10
    assert fill.route == "ibkr-paper"
    assert client.submitted["conid"] == 265598
    assert client.submitted["account_id"] == "DU1234567"


def test_ibkr_router_refuses_on_account_mode_mismatch():
    # paper mode configured, but the connected account looks like a live one
    client = _FakeIBKRClient(account_id="U1234567")
    router = oms.IBKRRouter(client, mode="paper")
    with pytest.raises(oms.OrderError, match="doesn't look like a paper account"):
        router.fill("buy", 10, "market", 100, None, symbol="AAPL")


def test_ibkr_router_refuses_when_conid_cannot_be_resolved():
    client = _FakeIBKRClient()
    router = oms.IBKRRouter(client, mode="paper")
    with pytest.raises(oms.OrderError, match="ibkr order failed"):
        router.fill("buy", 10, "market", 100, None, symbol="UNKNOWN")


def test_ibkr_router_wraps_submit_failures_as_order_error():
    client = _FakeIBKRClient(raise_on_submit=RuntimeError("gateway down"))
    router = oms.IBKRRouter(client, mode="paper")
    with pytest.raises(oms.OrderError, match="ibkr order failed"):
        router.fill("buy", 10, "market", 100, None, symbol="AAPL")


def test_ibkr_router_live_mode_route_name():
    client = _FakeIBKRClient(account_id="U1234567")
    router = oms.IBKRRouter(client, mode="live")
    fill = router.fill("buy", 1, "market", 100, None, symbol="AAPL")
    assert fill.route == "ibkr-live"

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
    result = risk_check.check_pretrade("AAPL", connect=lambda symbol: object())
    assert result.block_reason is None and not result.degraded

def test_risk_check_blocks_on_breach(monkeypatch):
    monkeypatch.setattr(risk_check, "check_pretrade", _REAL_CHECK_PRETRADE)
    monkeypatch.setattr(risk_check.qs, "run_query", lambda q, conn, limit: {
        "columns": ["time", "sym", "riskType", "limit", "exposure", "status", "shard"],
        "rows": [[1, "AAPL", "VAR", 1000000, 1200000, "BREACH", "s0"]],
    })
    result = risk_check.check_pretrade("AAPL", connect=lambda symbol: object())
    assert result.block_reason and "BREACH" in result.block_reason
    assert not result.degraded  # a verified read, not a degraded/unreachable one


def _boom(symbol):
    raise ConnectionRefusedError("no gateway")


def test_risk_check_fails_closed_by_default_when_feed_unreachable(monkeypatch):
    """Default policy (Settings.risk_gate_fail_open=False): an unreachable
    risk feed BLOCKS the order - "couldn't verify" is not "verified clean"."""
    monkeypatch.setattr(risk_check, "check_pretrade", _REAL_CHECK_PRETRADE)
    result = risk_check.check_pretrade("AAPL", connect=_boom, fail_open=False)
    assert result.block_reason and "unreachable" in result.block_reason
    assert result.degraded

def test_risk_check_fails_open_when_explicitly_configured(monkeypatch):
    """fail_open=True (RISK_GATE_FAIL_OPEN) is the opt-in override - order
    proceeds, but still flagged degraded so the caller can audit it."""
    monkeypatch.setattr(risk_check, "check_pretrade", _REAL_CHECK_PRETRADE)
    result = risk_check.check_pretrade("AAPL", connect=_boom, fail_open=True)
    assert result.block_reason is None
    assert result.degraded

def test_risk_check_default_policy_comes_from_settings(monkeypatch):
    """No explicit fail_open passed -> falls back to the deployment's
    configured policy, not a hardcoded default inside the function."""
    monkeypatch.setattr(risk_check, "check_pretrade", _REAL_CHECK_PRETRADE)
    monkeypatch.setattr(risk_check._settings, "risk_gate_fail_open", True)
    result = risk_check.check_pretrade("AAPL", connect=_boom)
    assert result.block_reason is None and result.degraded


# ---- real, live-data volatility check (alongside the simulated CRIMS one) -

def _fake_run_query_for(risk_row, prices):
    """Distinguishes the risk-table lookup from the trade-price lookup by
    query text, the same way the real q processes are distinguished by which
    table each query names."""
    def fn(q, conn, limit=None):
        if "from risk" in q:
            return {"columns": ["time", "sym", "riskType", "limit", "exposure", "status", "shard"],
                    "rows": [risk_row] if risk_row else []}
        return {"columns": ["price"], "rows": [[p] for p in prices]}
    return fn


def test_volatility_check_disabled_by_default_does_not_query_prices(monkeypatch):
    calls = []
    monkeypatch.setattr(risk_check.qs, "run_query",
                        lambda q, conn, limit=None: (calls.append(q), {"columns": [], "rows": []})[1])
    result = risk_check.check_realized_volatility("AAPL", connect=lambda s: object())
    assert result.block_reason is None
    assert calls == []  # short-circuited before touching qs.run_query at all


def test_volatility_check_blocks_on_extreme_moves(monkeypatch):
    # a violent sawtooth over a handful of prints -> huge realized vol
    prices = [100, 140, 90, 150, 80, 160, 70]
    monkeypatch.setattr(risk_check.qs, "run_query",
                        lambda q, conn, limit=None: {"columns": ["price"], "rows": [[p] for p in prices]})
    result = risk_check.check_realized_volatility("AAPL", connect=lambda s: object(), max_annualized=1.0)
    assert result.block_reason and "realized volatility" in result.block_reason
    assert result.realized_vol_annualized > 1.0


def test_volatility_check_passes_under_threshold(monkeypatch):
    prices = [100.0, 100.1, 100.0, 100.05, 100.02, 100.03]
    monkeypatch.setattr(risk_check.qs, "run_query",
                        lambda q, conn, limit=None: {"columns": ["price"], "rows": [[p] for p in prices]})
    result = risk_check.check_realized_volatility("AAPL", connect=lambda s: object(), max_annualized=5.0)
    assert result.block_reason is None
    assert result.realized_vol_annualized is not None


def test_volatility_check_unreachable_does_not_block(monkeypatch):
    """This is a supplementary signal, not the fail-closed backstop (that's
    the CRIMS check) - a live-data hiccup here must not itself halt trading."""
    monkeypatch.setattr(risk_check.qs, "run_query",
                        lambda q, conn, limit=None: (_ for _ in ()).throw(ConnectionRefusedError("down")))
    result = risk_check.check_realized_volatility("AAPL", connect=lambda s: object(), max_annualized=1.0)
    assert result.block_reason is None
    assert result.degraded


def test_pretrade_runs_volatility_check_when_crims_is_clean(monkeypatch):
    monkeypatch.setattr(risk_check, "check_pretrade", _REAL_CHECK_PRETRADE)
    monkeypatch.setattr(risk_check._settings, "risk_max_realized_vol_annualized", 1.0)
    monkeypatch.setattr(risk_check.qs, "run_query", _fake_run_query_for(
        risk_row=[1, "AAPL", "VAR", 1000000, 500000, "OK", "s0"],
        prices=[100, 140, 90, 150, 80, 160, 70]))
    result = risk_check.check_pretrade("AAPL", connect=lambda s: object())
    assert result.block_reason and "realized volatility" in result.block_reason


def test_pretrade_crims_breach_short_circuits_before_volatility_check(monkeypatch):
    """A CRIMS BREACH blocks immediately - the volatility check (and its own
    extra IPC round-trip) never needs to run."""
    monkeypatch.setattr(risk_check, "check_pretrade", _REAL_CHECK_PRETRADE)
    monkeypatch.setattr(risk_check._settings, "risk_max_realized_vol_annualized", 1.0)
    calls = []
    def fn(q, conn, limit=None):
        calls.append(q)
        return {"columns": ["time", "sym", "riskType", "limit", "exposure", "status", "shard"],
                "rows": [[1, "AAPL", "VAR", 1000000, 1200000, "BREACH", "s0"]]}
    monkeypatch.setattr(risk_check.qs, "run_query", fn)
    result = risk_check.check_pretrade("AAPL", connect=lambda s: object())
    assert result.block_reason and "BREACH" in result.block_reason
    assert len(calls) == 1  # only the risk-table lookup, no trade-price lookup


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
                        lambda symbol: risk_check.CheckResult(
                            block_reason=f"pre-trade risk check failed: {symbol} is in BREACH"))
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
