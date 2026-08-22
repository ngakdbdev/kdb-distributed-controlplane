"""Tests for app/backtest.py - the walk-forward replay of signal_engine's
actual live trend-following logic against historical trade prints."""
from datetime import datetime, timedelta, timezone

from app import backtest

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)


def _series(prices_by_minute: list[float], from_=BASE):
    return [{"time": (from_ + timedelta(minutes=i)).isoformat(), "price": p}
           for i, p in enumerate(prices_by_minute)]


def test_no_data_returns_empty_result():
    result = backtest.run_backtest([], "AAPL", BASE, BASE + timedelta(hours=1))
    assert result.trade_count == 0
    assert result.total_return_pct == 0.0
    assert result.win_rate is None
    assert result.max_drawdown_pct is None


def test_uptrend_then_downtrend_produces_one_winning_trade():
    # steady climb for 90 min (builds a real uptrend signal), then a sharp
    # drop that flips the trend down - should enter once, exit once, in profit.
    up = [100.0 * (1.01 ** i) for i in range(90)]
    down = [up[-1] * (0.97 ** i) for i in range(1, 30)]
    rows = _series(up + down)
    end = BASE + timedelta(minutes=len(up) + len(down))

    result = backtest.run_backtest(rows, "AAPL", BASE, end, stop_loss_pct=5.0,
                                   lookback_min=60, step_min=5)
    assert result.decision_points > 0
    assert result.trade_count >= 1
    assert result.trades[0].pnl_pct > 0
    assert result.trades[0].exit_reason in ("trend_flip", "stop_loss")


def test_crash_after_entry_produces_a_losing_exit():
    # Linear (not compounding-percentage) moves, so the numbers stay easy
    # to reason about: a modest climb to register a real "up" trend and
    # trigger entry, then a sharp, sustained drop back through and well
    # past any plausible entry price from the climb.
    up = [100.0 + i * 0.5 for i in range(65)]      # 100 -> ~132
    crash = [up[-1] - i * 3.0 for i in range(1, 15)]  # ~129 -> ~90
    rows = _series(up + crash)
    end = BASE + timedelta(minutes=len(up) + len(crash))

    result = backtest.run_backtest(rows, "AAPL", BASE, end, stop_loss_pct=2.0,
                                   lookback_min=60, step_min=5)
    assert result.trade_count >= 1
    # whichever rule caught it first (stop-loss or the trend flipping down),
    # a sustained post-entry crash must exit at a loss, not ride it out.
    assert result.trades[0].exit_reason in ("stop_loss", "trend_flip")
    assert result.trades[0].pnl_pct < 0


def test_flat_series_never_enters_a_position():
    rows = _series([100.0] * 90)
    end = BASE + timedelta(minutes=90)
    result = backtest.run_backtest(rows, "AAPL", BASE, end, lookback_min=60, step_min=5)
    assert result.trade_count == 0
    assert result.decision_points > 0  # forecast ran, just never saw an uptrend


def test_total_return_compounds_not_sums():
    result = backtest.BacktestResult(symbol="X", start="", end="", lookback_min=1, step_min=1,
                                     stop_loss_pct=1, total_prints=0, decision_points=0)
    result.trades = [
        backtest.BacktestTrade("t0", 100, "t1", 110, "trend_flip", 10.0),   # +10%
        backtest.BacktestTrade("t1", 110, "t2", 99, "stop_loss", -10.0),    # -10%
    ]
    # compounded: 1.10 * 0.90 = 0.99 -> -1%, NOT 10 - 10 = 0%
    assert round(result.total_return_pct, 2) == -1.0
    assert result.win_rate == 0.5


def test_max_drawdown_tracks_peak_to_trough():
    result = backtest.BacktestResult(symbol="X", start="", end="", lookback_min=1, step_min=1,
                                     stop_loss_pct=1, total_prints=0, decision_points=0)
    result.trades = [
        backtest.BacktestTrade("t0", 100, "t1", 120, "trend_flip", 20.0),   # equity 1.20 (new peak)
        backtest.BacktestTrade("t1", 120, "t2", 90, "stop_loss", -25.0),    # equity 0.90
    ]
    # drawdown from peak 1.20 to 0.90 = (1.20-0.90)/1.20 = 25%
    assert round(result.max_drawdown_pct, 1) == 25.0


def test_fetch_historical_trades_uses_injected_connection():
    calls = []

    class FakeConn:
        def __call__(self, query):
            calls.append(query)
            # RAW q-result shape (dict of column -> value list) - what
            # shape_result actually expects from a real connection, not the
            # already-shaped {columns, rows} grid run_query returns AFTER
            # processing it.
            return {"time": ["2026.01.01D00:01:00.000000000", "2026.01.01D00:00:00.000000000"],
                    "price": [101.0, 100.0]}

    def connect(shard_id):
        return FakeConn()

    rows = backtest.fetch_historical_trades(
        "AAPL", datetime(2026, 1, 1), datetime(2026, 1, 2), connect=connect)
    assert len(calls) == 1
    assert "AAPL" in calls[0]
    # sorted chronologically even though the fake returned them reversed
    assert rows[0]["price"] == 100.0 and rows[1]["price"] == 101.0
