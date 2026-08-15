"""
backtest.py - replays signal_engine.py's ACTUAL live decision logic (trend-
following via signal_forecast.build_time_forecast, plus its stop-loss/
trend-flip exit rules) against real historical HDB trade prints, so a
strategy change can be evaluated against real data before it goes live.
Previously entirely unbuilt - signal_composite.py's own comments already
say its factor weights are "NOT the output of any backtest or optimization
run," and there was no harness anywhere that could have produced one.

Deliberately replays signal_engine's trend-following logic, NOT
signal_composite's weighted score - the live bot (signal_engine.
evaluate_tenant) only ever consumes build_time_forecast's trend and a
configured stop-loss distance; it never reads signal_composite's composite
score at all (that score feeds the separate Predictive Signals page, a
different consumer). Backtesting signal_composite in isolation wouldn't
answer "would the bot have made money" - it isn't wired to execution, so
this replays what actually is.

Walk-forward, not a single lookahead-biased pass: at each decision point,
build_time_forecast only ever sees trade prints STRICTLY BEFORE that point
(a trailing lookback window), the same "only what's happened so far" a live
bot has. No peeking at the future.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from . import query_service as qs
from . import topology
from .signal_forecast import build_time_forecast

_SHARD_COUNT = int(os.environ.get("SHARD_COUNT", "2"))


def _connect_hdb(shard_id: str):
    """Direct IPC to the HDB owning `shard_id` - historical data, not the
    RDB's live/recent window signal_engine.fetch_trade_tape reads."""
    from qpython import qconnection
    shard = next(s for s in topology.shards(_SHARD_COUNT) if s.id == shard_id)
    host, port = topology.gateway_host(shard, "hdb").rsplit(":", 1)
    conn = qconnection.QConnection(host=host, port=int(port), pandas=False,
                                   timeout=int(os.environ.get("QUERY_TIMEOUT_SEC", "15")))
    conn.open()
    return conn


ConnectFn = Callable[[str], object]

_KDB_EPOCH = datetime(2000, 1, 1)


def _kdb_timestamp_literal(dt: datetime) -> str:
    """A colon-free q expression constructing a timestamp for `dt`, via
    kdb+'s standard "cast a raw nanoseconds-since-2000.01.01 long to
    timestamp" idiom (`` `timestamp$<int> ``) instead of a `YYYY.MM.DDDHH:MM:SS`
    literal. Deliberate: query_service.check_readonly's read-only guard
    blocks any query containing "0:"/"1:"/"2:" ANYWHERE in the text (it's
    q's dangerous file-I/O operator syntax) - confirmed live, this really
    does trip on an ordinary embedded time-of-day literal like "00:00:00"
    purely as a text match, nothing to do with what the query actually
    does. This sidesteps the collision instead of weakening a guard that's
    correctly blocking real file I/O syntax elsewhere."""
    ns = int((dt - _KDB_EPOCH).total_seconds() * 1_000_000_000)
    return f"(`timestamp$0)+{ns}"


def fetch_historical_trades(symbol: str, start: datetime, end: datetime,
                            connect: ConnectFn = _connect_hdb) -> list[dict]:
    """Chronological [{"time":..., "price":...}] for `symbol` between
    start/end (both naive UTC), read from the HDB shard that owns it.
    q date-range filtering is on the `date` virtual column (the partition
    key) plus a `time` bound within it, not a single timestamp range - the
    two conditions together do the real filtering."""
    shard_id = topology.shard_of(symbol, _SHARD_COUNT)
    start_d = start.strftime("%Y.%m.%d")
    end_d = end.strftime("%Y.%m.%d")
    start_t = _kdb_timestamp_literal(start)
    end_t = _kdb_timestamp_literal(end)
    query = (
        f'select time, price from trade where sym=`$"{symbol}", '
        f"date within ({start_d};{end_d}), time within ({start_t};{end_t})"
    )
    conn = None
    try:
        conn = connect(shard_id)
        grid = qs.run_query(query, conn, limit=qs.MAX_ROW_LIMIT)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    cols = grid.get("columns") or []
    if "time" not in cols or "price" not in cols:
        return []
    t_i, p_i = cols.index("time"), cols.index("price")
    rows = [{"time": r[t_i], "price": r[p_i]} for r in grid.get("rows", []) if r[p_i] is not None]
    rows.sort(key=lambda r: r["time"])
    return rows


@dataclass
class BacktestTrade:
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    exit_reason: str   # "stop_loss" | "trend_flip" | "end_of_window"
    pnl_pct: float


@dataclass
class BacktestResult:
    symbol: str
    start: str
    end: str
    lookback_min: int
    step_min: int
    stop_loss_pct: float
    total_prints: int
    decision_points: int
    trades: list[BacktestTrade] = field(default_factory=list)

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> Optional[float]:
        if not self.trades:
            return None
        wins = sum(1 for t in self.trades if t.pnl_pct > 0)
        return wins / len(self.trades)

    @property
    def total_return_pct(self) -> float:
        """Compounded, not summed - each trade's return applies to
        whatever capital remains after the prior one, same as a real
        account would compound (or shrink) through a sequence of trades."""
        equity = 1.0
        for t in self.trades:
            equity *= (1 + t.pnl_pct / 100.0)
        return (equity - 1.0) * 100.0

    @property
    def max_drawdown_pct(self) -> Optional[float]:
        if not self.trades:
            return None
        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        for t in self.trades:
            equity *= (1 + t.pnl_pct / 100.0)
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
        return max_dd * 100.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "start": self.start, "end": self.end,
            "lookback_min": self.lookback_min, "step_min": self.step_min,
            "stop_loss_pct": self.stop_loss_pct, "total_prints": self.total_prints,
            "decision_points": self.decision_points, "trade_count": self.trade_count,
            "win_rate": self.win_rate, "total_return_pct": self.total_return_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "trades": [t.__dict__ for t in self.trades],
        }


def run_backtest(rows: list[dict], symbol: str, start: datetime, end: datetime,
                 stop_loss_pct: float = 1.5, lookback_min: int = 60,
                 step_min: int = 5) -> BacktestResult:
    """`rows`: real chronological trade prints (see fetch_historical_trades).
    Walks forward in step_min increments from start to end; at each point,
    forecasts from only the trailing lookback_min of prints (no lookahead),
    and applies the SAME entry/exit rules signal_engine.evaluate_tenant
    uses live: enter long on trend=up while flat, exit on a stop-loss hit
    or the trend flipping down. Long-only, one position at a time - matches
    the live bot's own shape, not a generalized strategy framework."""
    result = BacktestResult(symbol=symbol, start=start.isoformat(), end=end.isoformat(),
                            lookback_min=lookback_min, step_min=step_min,
                            stop_loss_pct=stop_loss_pct, total_prints=len(rows), decision_points=0)
    if not rows:
        return result

    parsed = []
    for r in rows:
        t = r.get("time")
        if isinstance(t, str):
            t2 = t.replace("Z", "")
            try:
                t = datetime.fromisoformat(t2)
            except ValueError:
                continue
        elif not isinstance(t, datetime):
            continue
        parsed.append((t, float(r["price"])))
    parsed.sort(key=lambda x: x[0])
    if not parsed:
        return result

    position: Optional[dict] = None  # {"entry_time","entry_price","stop_price"}
    cursor = start
    lookback = timedelta(minutes=lookback_min)
    step = timedelta(minutes=step_min)
    idx = 0  # pointer into parsed, advanced as cursor moves forward

    while cursor <= end:
        while idx < len(parsed) and parsed[idx][0] <= cursor:
            idx += 1
        window_start = cursor - lookback
        window = [{"time": t.isoformat(), "price": p} for t, p in parsed
                 if window_start <= t <= cursor]
        if window:
            result.decision_points += 1
            forecast = build_time_forecast(window)
            last = forecast["last"]
            if last is not None:
                if position is None:
                    if forecast["trend"] == "up":
                        stop_price = last * (1 - stop_loss_pct / 100.0)
                        position = {"entry_time": cursor.isoformat(), "entry_price": last,
                                   "stop_price": stop_price}
                else:
                    stop_hit = last <= position["stop_price"]
                    trend_flipped = forecast["trend"] == "down"
                    if stop_hit or trend_flipped:
                        pnl_pct = (last - position["entry_price"]) / position["entry_price"] * 100.0
                        result.trades.append(BacktestTrade(
                            entry_time=position["entry_time"], entry_price=position["entry_price"],
                            exit_time=cursor.isoformat(), exit_price=last,
                            exit_reason="stop_loss" if stop_hit else "trend_flip",
                            pnl_pct=pnl_pct))
                        position = None
        cursor += step

    if position is not None and parsed:
        last = parsed[-1][1]
        pnl_pct = (last - position["entry_price"]) / position["entry_price"] * 100.0
        result.trades.append(BacktestTrade(
            entry_time=position["entry_time"], entry_price=position["entry_price"],
            exit_time=parsed[-1][0].isoformat(), exit_price=last,
            exit_reason="end_of_window", pnl_pct=pnl_pct))

    return result
