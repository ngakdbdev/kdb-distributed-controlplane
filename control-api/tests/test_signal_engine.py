"""Tests for signal_engine.py - the server-side port of Bot.jsx's momentum
strategy. Fakes the q connection layer (qs.run_query) the same way
test_trading.py fakes the risk feed - no real IPC in these tests."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app import signal_engine, topology
from app.models import BotConfig, BotLogEntry, BotPosition
from app.routers import trading as trading_router

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _uptrend(n=20, start=100.0, step_pct=0.01, from_=BASE):
    return [{"time": (from_ + timedelta(minutes=i)).isoformat(),
            "price": start * ((1 + step_pct) ** i), "size": 10} for i in range(n)]


def _flat(n=20, price=100.0, from_=BASE):
    return [{"time": (from_ + timedelta(minutes=i)).isoformat(), "price": price, "size": 10}
           for i in range(n)]


def _downtrend(n=20, start=100.0, step_pct=0.01, from_=BASE):
    return [{"time": (from_ + timedelta(minutes=i)).isoformat(),
            "price": start * ((1 - step_pct) ** i), "size": 10} for i in range(n)]


def _tape_by_shard(rows_by_symbol: dict) -> dict:
    by_shard = {}
    for sym, rows in rows_by_symbol.items():
        shard_id = topology.shard_of(sym, signal_engine._SHARD_COUNT)
        grid = by_shard.setdefault(shard_id, {"columns": ["time", "sym", "price", "size"], "rows": []})
        for r in rows:
            grid["rows"].append([r["time"], sym, r["price"], r["size"]])
    return by_shard


@pytest.fixture()
def fake_market(monkeypatch):
    """Returns a `set_tape(rows_by_symbol, universe=None)` setter. Fakes
    qs.run_query keyed by a fake connection that's just the shard id itself
    (fetch_trade_tape/fetch_universe_symbols never inspect the conn object,
    only pass it through to run_query - see signal_engine._connect_shard)."""
    state = {"tape": {}, "universe": {}}

    def fake_run_query(query, conn, limit=1000, **kwargs):
        shard_id = conn
        if query.strip().startswith("exec distinct sym"):
            vals = state["universe"].get(shard_id, [])
            return {"columns": ["value"], "rows": [[v] for v in vals]}
        return state["tape"].get(shard_id, {"columns": ["time", "sym", "price", "size"], "rows": []})

    monkeypatch.setattr(signal_engine, "qs", type("FakeQS", (), {"run_query": staticmethod(fake_run_query),
                                                                  "MAX_ROW_LIMIT": 10000}))

    def set_tape(rows_by_symbol, universe=None):
        state["tape"] = _tape_by_shard(rows_by_symbol)
        if universe is not None:
            by_shard = {}
            for sym in universe:
                by_shard.setdefault(topology.shard_of(sym, signal_engine._SHARD_COUNT), []).append(sym)
            state["universe"] = by_shard

    return lambda shard_id: shard_id, set_tape  # (connect, set_tape)


@pytest.fixture(autouse=True)
def no_real_risk_feed(monkeypatch):
    monkeypatch.setattr(trading_router.risk_check, "check_pretrade",
                        lambda symbol: trading_router.risk_check.CheckResult(block_reason=None))


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ---- fetch_trade_tape / fetch_universe_symbols / rank_by_momentum --------

def test_fetch_trade_tape_groups_by_owning_shard(fake_market):
    connect, set_tape = fake_market
    set_tape({"AAPL": _uptrend(5), "NVDA": _flat(5)})  # A-M -> s0, N-Z -> s1
    tape = signal_engine.fetch_trade_tape(["AAPL", "NVDA"], connect=connect)
    assert len(tape["AAPL"]) == 5 and len(tape["NVDA"]) == 5


def test_fetch_trade_tape_multi_symbol_same_shard_uses_semicolon_separated_casts(fake_market, monkeypatch):
    """Regression: `$"AAPL"`$"MSFT" (no separator) is a q 'type error, not an
    implicit list - confirmed live. Each symbol must be a `$"..."` cast (safe
    for hyphens/slashes like ETH-USD, BTC/USD) joined with ';' when more than
    one symbol maps to the same owning shard."""
    connect, set_tape = fake_market
    set_tape({"AAPL": _flat(3), "MSFT": _flat(3)})  # both A-M -> same shard (s0)
    seen_queries = []
    real_run_query = signal_engine.qs.run_query

    def spying_run_query(query, conn, limit=1000, **kwargs):
        seen_queries.append(query)
        return real_run_query(query, conn, limit=limit, **kwargs)
    monkeypatch.setattr(signal_engine.qs, "run_query", spying_run_query)

    signal_engine.fetch_trade_tape(["AAPL", "MSFT"], connect=connect)

    assert len(seen_queries) == 1  # one shard, one query
    assert '`$"AAPL";`$"MSFT"' in seen_queries[0] or '`$"MSFT";`$"AAPL"' in seen_queries[0]


def test_fetch_trade_tape_missing_symbol_is_empty(fake_market):
    connect, set_tape = fake_market
    set_tape({"AAPL": _uptrend(5)})
    tape = signal_engine.fetch_trade_tape(["AAPL", "ZZZZ"], connect=connect)
    assert tape["ZZZZ"] == []


def test_fetch_universe_symbols_merges_across_shards(fake_market):
    connect, set_tape = fake_market
    set_tape({}, universe=["AAPL", "MSFT", "NVDA", "ZEBRA"])
    universe = signal_engine.fetch_universe_symbols(connect=connect)
    assert universe == ["AAPL", "MSFT", "NVDA", "ZEBRA"]


def test_rank_by_momentum_orders_by_drift_descending(fake_market):
    connect, set_tape = fake_market
    set_tape({"AAPL": _uptrend(10), "MSFT": _downtrend(10), "NVDA": _flat(10)})
    ranked = signal_engine.rank_by_momentum(["AAPL", "MSFT", "NVDA"], connect=connect)
    assert [r["symbol"] for r in ranked] == ["AAPL", "NVDA", "MSFT"]
    assert ranked[0]["trend"] == "up" and ranked[-1]["trend"] == "down"


def test_rank_by_momentum_drops_symbols_with_no_data(fake_market):
    connect, set_tape = fake_market
    set_tape({"AAPL": _uptrend(10)})  # MSFT has no rows at all
    ranked = signal_engine.rank_by_momentum(["AAPL", "MSFT"], connect=connect)
    assert [r["symbol"] for r in ranked] == ["AAPL"]


# ---- evaluate_tenant: manual mode -----------------------------------------

def test_opens_long_on_uptrend_with_sufficient_risk_budget(session, fake_market):
    connect, set_tape = fake_market
    set_tape({"AAPL": _uptrend(20)})
    config = BotConfig(tenant_id=1, mode="manual", symbols_json='["AAPL"]',
                       paper_capital=10000, risk_pct=1.0, stop_loss_pct=1.5)
    session.add(config); session.commit()

    signal_engine.evaluate_tenant(session, config, connect=connect)

    positions = session.exec(select(BotPosition).where(BotPosition.tenant_id == 1)).all()
    assert len(positions) == 1 and positions[0].symbol == "AAPL" and positions[0].qty > 0
    logs = session.exec(select(BotLogEntry).where(BotLogEntry.tenant_id == 1)).all()
    assert any(l.type == "open" for l in logs)


def test_holds_flat_without_opening(session, fake_market):
    connect, set_tape = fake_market
    set_tape({"AAPL": _flat(20)})
    config = BotConfig(tenant_id=1, mode="manual", symbols_json='["AAPL"]')
    session.add(config); session.commit()

    signal_engine.evaluate_tenant(session, config, connect=connect)

    assert session.exec(select(BotPosition).where(BotPosition.tenant_id == 1)).all() == []
    logs = session.exec(select(BotLogEntry).where(BotLogEntry.tenant_id == 1)).all()
    assert logs and logs[0].type == "hold"


def test_closes_on_stop_loss_hit(session, fake_market):
    connect, set_tape = fake_market
    # already holding AAPL at entry 100, stop 98.5 - feed a flat tape whose
    # last print is below the stop
    session.add(BotConfig(tenant_id=1, mode="manual", symbols_json='["AAPL"]'))
    session.add(BotPosition(tenant_id=1, symbol="AAPL", qty=10, entry_price=100.0, stop_price=98.5))
    session.commit()
    config = session.exec(select(BotConfig).where(BotConfig.tenant_id == 1)).first()

    set_tape({"AAPL": _flat(20, price=98.0)})  # flat trend, but under the stop
    signal_engine.evaluate_tenant(session, config, connect=connect)

    assert session.exec(select(BotPosition).where(BotPosition.tenant_id == 1)).all() == []
    logs = session.exec(select(BotLogEntry).where(BotLogEntry.tenant_id == 1)).all()
    assert any(l.type in ("close-win", "close-loss") for l in logs)
    assert any("stop-loss hit" in l.reason for l in logs)


def test_closes_on_trend_flip_even_above_stop(session, fake_market):
    session.add(BotConfig(tenant_id=1, mode="manual", symbols_json='["AAPL"]'))
    session.add(BotPosition(tenant_id=1, symbol="AAPL", qty=10, entry_price=100.0, stop_price=50.0))
    session.commit()
    config = session.exec(select(BotConfig).where(BotConfig.tenant_id == 1)).first()

    connect, set_tape = fake_market
    set_tape({"AAPL": _downtrend(20, start=100.0)})  # last stays well above stop=50, but trend is down
    signal_engine.evaluate_tenant(session, config, connect=connect)

    assert session.exec(select(BotPosition).where(BotPosition.tenant_id == 1)).all() == []
    logs = session.exec(select(BotLogEntry).where(BotLogEntry.tenant_id == 1)).all()
    assert any("trend flipped down" in l.reason for l in logs)


def test_holds_position_when_neither_stop_nor_trend_flip(session, fake_market):
    session.add(BotConfig(tenant_id=1, mode="manual", symbols_json='["AAPL"]'))
    session.add(BotPosition(tenant_id=1, symbol="AAPL", qty=10, entry_price=100.0, stop_price=90.0))
    session.commit()
    config = session.exec(select(BotConfig).where(BotConfig.tenant_id == 1)).first()

    connect, set_tape = fake_market
    set_tape({"AAPL": _uptrend(20, start=100.0)})
    signal_engine.evaluate_tenant(session, config, connect=connect)

    positions = session.exec(select(BotPosition).where(BotPosition.tenant_id == 1)).all()
    assert len(positions) == 1  # still held


def test_risk_budget_shared_across_symbols_in_one_pass(session, fake_market):
    """A small paper_capital + 1% risk cap should only afford ONE of two
    simultaneously-opening symbols, not both - the risk budget must be
    checked against the WORKING (in-pass) total, not just what was already
    in the DB before this pass started."""
    connect, set_tape = fake_market
    set_tape({"AAPL": _uptrend(20), "MSFT": _uptrend(20, start=50.0)})
    config = BotConfig(tenant_id=1, mode="manual", symbols_json='["AAPL","MSFT"]',
                       paper_capital=100, risk_pct=1.0, stop_loss_pct=1.5)  # $1 total risk cap
    session.add(config); session.commit()

    signal_engine.evaluate_tenant(session, config, connect=connect)

    positions = session.exec(select(BotPosition).where(BotPosition.tenant_id == 1)).all()
    assert len(positions) <= 1
    logs = session.exec(select(BotLogEntry).where(BotLogEntry.tenant_id == 1)).all()
    if len(positions) == 1:
        assert any(l.type == "skip" and "risk budget exhausted" in l.reason for l in logs)


def test_risk_pct_is_clamped_to_hard_cap_even_if_row_says_more(session, fake_market):
    """Defence in depth: even if a BotConfig row somehow has risk_pct > the
    hard cap (e.g. written before a cap change), evaluate_tenant re-clamps it
    rather than trusting the stored value."""
    connect, set_tape = fake_market
    set_tape({"AAPL": _uptrend(20)})
    config = BotConfig(tenant_id=1, mode="manual", symbols_json='["AAPL"]',
                       paper_capital=10000, risk_pct=50.0, stop_loss_pct=1.5)
    session.add(config); session.commit()

    signal_engine.evaluate_tenant(session, config, connect=connect)

    positions = session.exec(select(BotPosition).where(BotPosition.tenant_id == 1)).all()
    assert len(positions) == 1
    pos = positions[0]
    risked = pos.qty * (pos.entry_price - pos.stop_price)
    # at the clamped 1% cap, not the stored 50%
    assert risked <= 10000 * (signal_engine.MAX_RISK_PCT / 100.0) + 1e-6


def test_manual_mode_basket_capped_at_max_basket(session, fake_market):
    connect, set_tape = fake_market
    too_many = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"]  # 7 > MAX_BASKET (6)
    set_tape({s: _flat(5) for s in too_many})
    config = BotConfig(tenant_id=1, mode="manual", symbols_json=str(too_many).replace("'", '"'))
    session.add(config); session.commit()

    signal_engine.evaluate_tenant(session, config, connect=connect)

    logs = {l.symbol for l in session.exec(select(BotLogEntry).where(BotLogEntry.tenant_id == 1)).all()}
    assert len(logs) <= signal_engine.MAX_BASKET


def test_no_symbols_configured_is_a_noop(session, fake_market):
    connect, _ = fake_market
    config = BotConfig(tenant_id=1, mode="manual", symbols_json="[]")
    session.add(config); session.commit()
    signal_engine.evaluate_tenant(session, config, connect=connect)
    assert session.exec(select(BotLogEntry).where(BotLogEntry.tenant_id == 1)).all() == []


# ---- evaluate_tenant: auto mode -------------------------------------------

def test_auto_mode_screens_universe_and_opens_top_uptrend(session, fake_market):
    connect, set_tape = fake_market
    set_tape({"AAPL": _uptrend(20), "BBBB": _downtrend(20), "CCCC": _flat(20)},
             universe=["AAPL", "BBBB", "CCCC"])
    config = BotConfig(tenant_id=1, mode="auto", max_positions=2,
                       paper_capital=10000, risk_pct=1.0, stop_loss_pct=1.5)
    session.add(config); session.commit()

    signal_engine.evaluate_tenant(session, config, connect=connect)

    positions = session.exec(select(BotPosition).where(BotPosition.tenant_id == 1)).all()
    assert [p.symbol for p in positions] == ["AAPL"]  # only the up-trending one


def test_auto_mode_respects_max_positions_cap(session, fake_market):
    connect, set_tape = fake_market
    set_tape({"AAAA": _uptrend(20, start=10), "BBBB": _uptrend(20, start=20),
              "CCCC": _uptrend(20, start=30)},
             universe=["AAAA", "BBBB", "CCCC"])
    config = BotConfig(tenant_id=1, mode="auto", max_positions=1,
                       paper_capital=100000, risk_pct=1.0, stop_loss_pct=1.5)
    session.add(config); session.commit()

    signal_engine.evaluate_tenant(session, config, connect=connect)

    positions = session.exec(select(BotPosition).where(BotPosition.tenant_id == 1)).all()
    assert len(positions) == 1


def test_auto_mode_universe_screen_failure_is_logged_not_raised(session, fake_market, monkeypatch):
    connect, _ = fake_market
    monkeypatch.setattr(signal_engine, "fetch_universe_symbols",
                        lambda connect=None: (_ for _ in ()).throw(ConnectionRefusedError("no gateway")))
    config = BotConfig(tenant_id=1, mode="auto")
    session.add(config); session.commit()

    signal_engine.evaluate_tenant(session, config, connect=connect)  # must not raise

    logs = session.exec(select(BotLogEntry).where(BotLogEntry.tenant_id == 1)).all()
    assert any(l.type == "error" and "universe screen failed" in l.reason for l in logs)


# ---- order placement goes through the real risk gate ----------------------

def test_order_blocked_by_risk_gate_is_logged_as_error_not_opened(session, fake_market, monkeypatch):
    monkeypatch.setattr(trading_router.risk_check, "check_pretrade",
                        lambda symbol: trading_router.risk_check.CheckResult(
                            block_reason=f"pre-trade risk check failed: {symbol} is in BREACH"))
    connect, set_tape = fake_market
    set_tape({"AAPL": _uptrend(20)})
    config = BotConfig(tenant_id=1, mode="manual", symbols_json='["AAPL"]')
    session.add(config); session.commit()

    signal_engine.evaluate_tenant(session, config, connect=connect)

    assert session.exec(select(BotPosition).where(BotPosition.tenant_id == 1)).all() == []
    logs = session.exec(select(BotLogEntry).where(BotLogEntry.tenant_id == 1)).all()
    assert any(l.type == "error" and "order blocked" in l.reason and "BREACH" in l.reason for l in logs)
