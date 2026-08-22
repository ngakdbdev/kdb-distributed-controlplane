"""Endpoint tests for routers/signals.py - the Predictive Signals page's
backend. Trade tape and news are faked (no live cluster/network needed),
same pattern test_bot_router.py already uses for signal_engine."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app.main as m
from app.db import engine
from app.models import Position
from app.routers import signals as signals_router


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(autouse=True)
def fake_market_data(monkeypatch):
    """Every endpoint below needs SOME trade tape / news to be interesting -
    fake both so tests never depend on a live cluster or the network."""
    def fake_tape(symbols):
        return {sym: [{"time": "2026-01-01T00:00:00Z", "price": 100.0 + i, "size": 10}
                     for i in range(25)] for sym in symbols}
    monkeypatch.setattr(signals_router.signal_engine, "fetch_trade_tape", fake_tape)

    def fake_news(symbols=None, limit=30):
        return {"items": [], "sources": {"finnhub": False, "alphavantage": False}}
    monkeypatch.setattr(signals_router.news_feed, "fetch_news", fake_news)
    monkeypatch.setattr(signals_router.news_feed, "sentiment_for_symbol",
                        lambda sym, news_items=None: {"symbol": sym, "score": 0.0, "n": 0})


def test_predictive_requires_auth(client):
    assert client.get("/signals/predictive").status_code in (401, 403)


def test_predictive_returns_scored_signals_for_requested_symbols(client, tadmin):
    r = client.get("/signals/predictive?symbols=AAPL,MSFT", headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbols_requested"] == 2
    assert body["symbols_evaluated"] == 2
    syms = {row["symbol"] for row in body["signals"]}
    assert syms == {"AAPL", "MSFT"}
    assert "weights" in body and "momentum" in body["weights"]


def test_tenant_default_symbols_uses_manual_bot_basket_when_present():
    # Unit-tested directly against an isolated in-memory DB rather than the
    # shared demo tenant over HTTP - this suite shares one DB across every
    # test FILE with no per-test reset (see conftest.py), and another
    # file's tests may already have an open position pinning a symbol into
    # the shared tenant's basket (confirmed live: routers/bot.py refuses to
    # remove a basket symbol with an open position), which makes "PUT a
    # specific basket, then assert it verbatim" unsafe there. An isolated
    # session sidesteps all of that for what's really a pure function of
    # "what's in this tenant's BotConfig row".
    from sqlmodel import Session as SQLSession
    from sqlmodel import SQLModel, create_engine

    from app.models import BotConfig
    from app.routers import auth as auth_module
    from app.routers import signals as sig

    engine_iso = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine_iso)
    with SQLSession(engine_iso) as s:
        s.add(BotConfig(tenant_id=1, mode="manual", symbols_json='["SIGNVDA", "SIGIBM"]'))
        s.commit()
        user = auth_module.CurrentUser(user_id=1, tenant_id=1, role="tenant_admin", email="x@example.com")
        assert sig._tenant_default_symbols(user, s) == ["SIGNVDA", "SIGIBM"]


def test_tenant_default_symbols_falls_back_when_auto_mode_or_no_config():
    from sqlmodel import Session as SQLSession
    from sqlmodel import SQLModel, create_engine

    from app.models import BotConfig
    from app.routers import auth as auth_module
    from app.routers import signals as sig

    engine_iso = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine_iso)
    user = auth_module.CurrentUser(user_id=1, tenant_id=1, role="tenant_admin", email="x@example.com")
    with SQLSession(engine_iso) as s:
        # no BotConfig row at all yet
        assert sig._tenant_default_symbols(user, s) == sig._DEFAULT_SYMBOLS

    with SQLSession(engine_iso) as s:
        s.add(BotConfig(tenant_id=1, mode="auto", symbols_json='["SIGNVDA"]'))
        s.commit()
        # auto mode's basket changes at runtime (it screens the live
        # universe) - there's no fixed list to trust here, so this must
        # NOT return the stale symbols_json auto mode doesn't actually use
        assert sig._tenant_default_symbols(user, s) == sig._DEFAULT_SYMBOLS


def test_predictive_falls_back_to_default_symbols_when_no_bot_config(client, tadmin):
    r = client.get("/signals/predictive", headers=tadmin)
    assert r.status_code == 200, r.text
    assert r.json()["symbols_requested"] > 0


def test_predictive_skips_symbols_with_no_real_data(client, tadmin, monkeypatch):
    monkeypatch.setattr(signals_router.signal_engine, "fetch_trade_tape",
                        lambda symbols: {s: [] for s in symbols})  # nothing tradeable
    r = client.get("/signals/predictive?symbols=ZZZZ", headers=tadmin)
    assert r.status_code == 200, r.text
    assert r.json()["symbols_evaluated"] == 0


def test_news_requires_auth(client):
    assert client.get("/signals/news").status_code in (401, 403)


def test_news_returns_items_and_sources(client, tadmin, monkeypatch):
    monkeypatch.setattr(signals_router.news_feed, "fetch_news",
                        lambda symbols=None, limit=30: {
                            "items": [{"headline": "Test headline", "sentiment_score": 0.5}],
                            "sources": {"finnhub": True, "alphavantage": False}})
    r = client.get("/signals/news?symbols=AAPL&limit=5", headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"][0]["headline"] == "Test headline"
    assert body["sources"]["finnhub"] is True


def test_news_limit_is_clamped(client, tadmin, monkeypatch):
    captured = {}

    def fake(symbols=None, limit=30):
        captured["limit"] = limit
        return {"items": [], "sources": {}}
    monkeypatch.setattr(signals_router.news_feed, "fetch_news", fake)
    client.get("/signals/news?limit=99999", headers=tadmin)
    assert captured["limit"] == 100


def test_portfolio_sentiment_requires_auth(client):
    assert client.get("/signals/portfolio-sentiment").status_code in (401, 403)


def test_portfolio_sentiment_matches_zero_when_no_positions_exist(client, tadmin):
    # This suite shares one DB/demo tenant across every test FILE (see
    # conftest.py - no per-test reset), so other files' tests may have
    # already created real Position rows for this tenant by the time this
    # one runs - asserting a hardcoded "n_positions == 0" is unsafe here.
    # Query the DB directly for ground truth instead of assuming a clean
    # slate (same "don't touch/assume state another file owns" discipline
    # test_bot_router.py's ZBOT symbol already follows).
    with Session(engine) as s:
        login = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
        tenant_id = login.json()["tenant_id"]
        existing = s.exec(select(Position).where(Position.tenant_id == tenant_id, Position.qty != 0)).all()

    r = client.get("/signals/portfolio-sentiment", headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n_positions"] == len(existing)
    if not existing:
        assert body["weighted_sentiment"] == 0.0


def test_portfolio_sentiment_weights_by_market_value(client, tadmin, monkeypatch):
    # Unique symbols unlikely to collide with any other test file (same
    # discipline as test_bot_router.py's ZBOT), PLUS a neutral fake
    # sentiment for every OTHER symbol some other file may have created a
    # position in for this shared tenant - so this test's math is exact
    # regardless of what state already exists.
    login = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    tenant_id = login.json()["tenant_id"]
    with Session(engine) as s:
        s.add(Position(tenant_id=tenant_id, symbol="SIGBIGPOS", qty=100, avg_price=100.0))   # value 10,000
        s.add(Position(tenant_id=tenant_id, symbol="SIGSMALLPOS", qty=1, avg_price=10.0))    # value 10
        s.commit()
        all_positions = s.exec(select(Position).where(Position.tenant_id == tenant_id, Position.qty != 0)).all()

    scores = {"SIGBIGPOS": 1.0, "SIGSMALLPOS": -1.0}

    def fake_sentiment(sym, news_items=None):
        return {"symbol": sym, "score": scores.get(sym, 0.0), "n": 1}
    monkeypatch.setattr(signals_router.news_feed, "sentiment_for_symbol", fake_sentiment)

    expected_weighted_sum = sum(scores.get(p.symbol, 0.0) * abs(p.qty * p.avg_price) for p in all_positions)
    expected_weight_total = sum(abs(p.qty * p.avg_price) for p in all_positions)
    expected = expected_weighted_sum / expected_weight_total if expected_weight_total else 0.0

    r = client.get("/signals/portfolio-sentiment", headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n_positions"] == len(all_positions)
    assert body["weighted_sentiment"] == pytest.approx(expected, abs=1e-4)  # endpoint rounds to 4dp
    # SIGBIGPOS's 10,000 market value should still dominate SIGSMALLPOS's 10,
    # regardless of whatever else is mixed in from other test files
    assert body["weighted_sentiment"] > 0.0
