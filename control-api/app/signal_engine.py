"""
signal_engine.py - server-side promotion of the paper trading bot's momentum
strategy, previously only in web-ui/src/pages/Bot.jsx (see that file's own
docstring: "it runs entirely in this browser tab ... forgets its open
positions/log the moment the tab closes"). This is the exact same strategy -
same caps, same thresholds, same risk-budget math - evaluated per enabled
tenant on a server-side interval (app/bot_scheduler.py) instead of a
setInterval in someone's browser, with state persisted in BotPosition /
BotLogEntry (app/models.py) instead of React state / localStorage.

Orders are placed through routers/trading.place_market_order_internal - the
exact path a human's market order takes, including the pre-trade risk gate
(app/risk_check.py). The bot never gets a shortcut around that.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable, Optional

from sqlmodel import Session, select

from . import alpaca_broker
from . import oms
from . import query_service as qs
from . import topology
from .models import BotConfig, BotLogEntry, BotPosition
from .routers.trading import place_market_order_internal
from .signal_forecast import build_time_forecast

log = logging.getLogger("signal_engine")

# Same hard caps Bot.jsx enforced client-side - kept here as the single
# source of truth now that both routers/bot.py (validating writes) and this
# module (evaluating them) need them.
MAX_RISK_PCT = 1.0            # "allowed risk is 1% of the capital" - hard cap regardless of input
MAX_BASKET = 6                # manual mode basket size
MAX_POSITIONS_CAP = 10        # auto mode concurrent-position ceiling
UNIVERSE_SCAN_CAP = 30        # auto mode - how many distinct symbols get a tape pulled per screen
TAPE_LIMIT_PER_SHARD = 1200

_SHARD_COUNT = int(os.environ.get("SHARD_COUNT", "2"))

ConnectFn = Callable[[str], object]

# How long a broker-tradability check is trusted before re-checking - an
# asset's tradable status doesn't change minute to minute, and this module
# runs on a tight poll (bot_scheduler.py's BOT_POLL_SEC, ~12s). Confirmed
# live: without this cache, a symbol the broker rejects gets re-checked -
# and, without the check at all, re-ATTEMPTED as a real doomed order - on
# literally every single poll, forever.
_TRADABILITY_TTL_SEC = 3600
_tradability_cache: dict[str, tuple[bool, float]] = {}


def _is_broker_tradable(symbol: str) -> bool:
    """True if there's no real broker to check against (PaperRouter accepts
    any symbol) or the configured broker confirms it lists this one. This
    platform's own auto-mode screens the cluster's ACTUAL live symbol
    universe, which includes symbols from simulated/demo feeds that were
    never meant to be tradable anywhere real - without this check, the bot
    would keep trying to open a real Alpaca position in one of those and
    getting rejected, forever, one wasted order attempt per poll."""
    client = alpaca_broker.client_from_env()
    if client is None:
        return True
    now = time.monotonic()
    cached = _tradability_cache.get(symbol)
    if cached is not None and (now - cached[1]) < _TRADABILITY_TTL_SEC:
        return cached[0]
    tradable = client.is_tradable(symbol)
    _tradability_cache[symbol] = (tradable, now)
    return tradable


def _connect_shard(shard_id: str):
    """Open a direct IPC connection to the RDB owning `shard_id`. Queried
    directly (not via the query workspace's HTTP round-trip) since this runs
    server-side already, in-process - the same shard-routing topology.py
    uses everywhere else (risk_check.py, the gateway itself)."""
    from qpython import qconnection
    shard = next(s for s in topology.shards(_SHARD_COUNT) if s.id == shard_id)
    host, port = topology.gateway_host(shard, "rdb").rsplit(":", 1)
    conn = qconnection.QConnection(host=host, port=int(port), pandas=False,
                                   timeout=int(os.environ.get("QUERY_TIMEOUT_SEC", "15")))
    conn.open()
    return conn


def _group_by_shard(symbols) -> dict:
    groups: dict = {}
    for sym in symbols:
        groups.setdefault(topology.shard_of(sym, _SHARD_COUNT), []).append(sym)
    return groups


def fetch_trade_tape(symbols, connect: ConnectFn = _connect_shard,
                     limit_per_shard: int = TAPE_LIMIT_PER_SHARD) -> dict:
    """{SYMBOL: [{time, price, size}]} for the given symbols, read directly
    off the RDB shard(s) that own them (grouped so each owning shard is asked
    only once). Mirrors what web-ui/src/lib/tradingCore.js's fetchTradeTape
    reads through the query workspace - same trade table, same recent-rows
    shape - just fetched in-process instead of over HTTP+IPC."""
    out = {s: [] for s in symbols}
    for shard_id, syms in _group_by_shard(symbols).items():
        # `$"..."` casts a string to a symbol - the only safe way to embed an
        # arbitrary symbol as a q literal. A bare backtick token (`ETH-USD)
        # is NOT safe: kdb+ parses the hyphen as subtraction (`ETH minus a
        # variable USD), which errors with "'USD" for any symbol containing
        # a hyphen - confirmed live once crypto symbols (ETH-USD, BTC/USD)
        # started flowing through this table. List elements need a semicolon
        # between them too - bare juxtaposition of several `$"..."` casts
        # (no separator) is a 'type error, not an implicit list.
        sym_list = ";".join(f'`$"{s}"' for s in syms)
        query = f"select time, sym, price, size from trade where sym in ({sym_list})"
        conn = None
        try:
            conn = connect(shard_id)
            grid = qs.run_query(query, conn, limit=limit_per_shard)
        except Exception as exc:  # noqa: BLE001 - one bad shard must not sink the whole pass
            log.warning("trade tape fetch failed for shard %s: %s", shard_id, exc)
            continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        cols = grid["columns"]
        idx = {c: cols.index(c) for c in ("time", "sym", "price", "size") if c in cols}
        for row in grid.get("rows", []):
            sym = str(row[idx["sym"]]) if "sym" in idx else None
            if sym not in out:
                continue
            out[sym].append({
                "time": row[idx["time"]] if "time" in idx else None,
                "price": (float(row[idx["price"]]) if "price" in idx and row[idx["price"]] is not None else None),
                "size": (float(row[idx["size"]]) if "size" in idx and row[idx["size"]] is not None else None),
            })
    return out


def fetch_universe_symbols(connect: ConnectFn = _connect_shard) -> list:
    """Distinct symbols currently trading, across every shard - mirrors the
    auto-mode universe scan (`exec distinct sym from trade`) Bot.jsx issued
    against the gateway, here issued directly per shard (each shard only
    ever owns its own letter-range slice of the symbol space anyway, so a
    per-shard scan plus a merge is equivalent, without a gateway hop)."""
    out = set()
    for shard in topology.shards(_SHARD_COUNT):
        conn = None
        try:
            conn = connect(shard.id)
            grid = qs.run_query("exec distinct sym from trade", conn, limit=qs.MAX_ROW_LIMIT)
        except Exception as exc:  # noqa: BLE001
            log.warning("universe scan failed for shard %s: %s", shard.id, exc)
            continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        cols = grid.get("columns") or []
        idx = cols.index("value") if "value" in cols else 0
        out.update(str(r[idx]) for r in grid.get("rows", []) if r)
    return sorted(out)


def rank_by_momentum(symbols, connect: ConnectFn = _connect_shard) -> list:
    """Rank candidate symbols by real per-minute drift, most bullish first -
    same shape as Bot.jsx's rankByMomentum."""
    if not symbols:
        return []
    tape = fetch_trade_tape(symbols, connect=connect)
    ranked = []
    for symbol in symbols:
        forecast = build_time_forecast(tape.get(symbol, []))
        if forecast["last"] is None:
            continue
        ranked.append({
            "symbol": symbol, "trend": forecast["trend"],
            "drift_per_min": forecast.get("drift_per_min") or 0.0, "last": forecast["last"],
        })
    ranked.sort(key=lambda r: r["drift_per_min"], reverse=True)
    return ranked


def _log(session: Session, tenant_id: int, symbol: Optional[str], type_: str, reason: str) -> None:
    session.add(BotLogEntry(tenant_id=tenant_id, symbol=symbol, type=type_, reason=reason))


def evaluate_tenant(session: Session, config: BotConfig, connect: ConnectFn = _connect_shard) -> None:
    """One decision pass for one tenant's bot. A direct port of Bot.jsx's
    evaluateAll(): momentum-following, long-only, sized so a stop-loss hit
    costs no more than riskPct% of paper capital in aggregate across every
    position the bot currently has open (not a fresh cap per symbol).
    Whatever the bot already holds keeps being monitored for its exit
    regardless of mode or whether it's still top-ranked. Commits its own
    session state; a failure partway through is logged by the caller
    (bot_scheduler.py) and rolled back, same as any other pass."""
    tenant_id = config.tenant_id
    held = {p.symbol: p for p in session.exec(
        select(BotPosition).where(BotPosition.tenant_id == tenant_id)).all()}
    held_symbols = list(held.keys())

    if config.mode == "auto":
        try:
            universe = fetch_universe_symbols(connect=connect)[:UNIVERSE_SCAN_CAP]
            ranked = rank_by_momentum(universe, connect=connect)
        except Exception as exc:  # noqa: BLE001
            _log(session, tenant_id, None, "error", f"universe screen failed: {exc}")
            session.commit()
            return
        max_positions = min(MAX_POSITIONS_CAP, max(1, config.max_positions))
        open_slots = max(0, max_positions - len(held_symbols))
        picks = [r["symbol"] for r in ranked
                if r["trend"] == "up" and r["symbol"] not in held_symbols][:open_slots]
        symbols = list(dict.fromkeys(held_symbols + picks))
        if not symbols:
            session.commit()
            return
    else:
        configured = json.loads(config.symbols_json or "[]")
        symbols = list(dict.fromkeys(s.upper() for s in configured if s))[:MAX_BASKET]
        if not symbols:
            session.commit()
            return

    risk_pct = min(MAX_RISK_PCT, max(0.0, config.risk_pct))
    stop_loss_pct = max(0.1, config.stop_loss_pct)
    total_risk_cap = max(0.0, config.paper_capital) * (risk_pct / 100.0)
    # local working copy, kept in lock-step as positions open/close within
    # this pass - two symbols opening in the SAME pass must not both size
    # against the same "available" budget and jointly overspend it.
    working = dict(held)

    try:
        tape = fetch_trade_tape(symbols, connect=connect)
    except Exception as exc:  # noqa: BLE001
        _log(session, tenant_id, None, "error", str(exc))
        session.commit()
        return

    for symbol in symbols:
        forecast = build_time_forecast(tape.get(symbol, []))
        last = forecast["last"]
        if last is None:
            _log(session, tenant_id, symbol, "skip", f"no recent trades for {symbol} yet")
            continue

        pos = working.get(symbol)
        if pos is None:
            if forecast["trend"] != "up":
                _log(session, tenant_id, symbol, "hold",
                    f"flat, trend is {forecast['trend']} - waiting for momentum up")
                continue

            if not _is_broker_tradable(symbol):
                _log(session, tenant_id, symbol, "skip",
                    f"{symbol} isn't tradable on the configured broker - not attempting an order")
                continue

            stop_price = last * (1 - stop_loss_pct / 100.0)
            stop_distance = last - stop_price
            used_risk = sum(p.qty * (p.entry_price - p.stop_price) for p in working.values())
            available_risk = max(0.0, total_risk_cap - used_risk)
            qty = int(available_risk // stop_distance) if stop_distance > 0 else 0
            if qty < 1:
                _log(session, tenant_id, symbol, "skip",
                    f"risk budget exhausted - {available_risk:.2f} available of "
                    f"{total_risk_cap:.2f} total cap ({len(working)} position(s) already open), "
                    f"not enough for a 1-share stop distance of {stop_distance:.4f} on {symbol}")
                continue

            try:
                order = place_market_order_internal(
                    session, tenant_id, f"signal-bot:{tenant_id}", symbol, "buy", qty, last)
            except oms.OrderError as exc:
                _log(session, tenant_id, symbol, "error", f"order blocked: {exc}")
                continue

            new_pos = BotPosition(tenant_id=tenant_id, symbol=symbol, qty=order.qty,
                                  entry_price=order.fill_price or last, stop_price=stop_price,
                                  order_id=order.id)
            session.add(new_pos)
            working[symbol] = new_pos
            _log(session, tenant_id, symbol, "open",
                f"{'screened + ' if config.mode == 'auto' else ''}momentum up - "
                f"bought {new_pos.qty} @ {new_pos.entry_price:.4f}, stop {stop_price:.4f} "
                f"(risking {new_pos.qty * stop_distance:.2f} of {available_risk:.2f} available, "
                f"{total_risk_cap:.2f} total cap)")
            continue

        stop_hit = last <= pos.stop_price
        trend_flipped = forecast["trend"] == "down"
        if not (stop_hit or trend_flipped):
            _log(session, tenant_id, symbol, "hold",
                f"long {pos.qty} @ {pos.entry_price:.4f}, stop {pos.stop_price:.4f}, "
                f"last {last:.4f} - holding")
            continue

        try:
            order = place_market_order_internal(
                session, tenant_id, f"signal-bot:{tenant_id}", symbol, "sell", pos.qty, last)
        except oms.OrderError as exc:
            _log(session, tenant_id, symbol, "error", f"close blocked: {exc}")
            continue

        fill_price = order.fill_price or last
        pnl = (fill_price - pos.entry_price) * pos.qty
        session.delete(pos)
        del working[symbol]
        _log(session, tenant_id, symbol, "close-win" if pnl >= 0 else "close-loss",
            f"{'stop-loss hit' if stop_hit else 'trend flipped down'} - "
            f"sold {pos.qty} @ {fill_price:.4f}, P&L {pnl:.2f}")

    session.commit()
