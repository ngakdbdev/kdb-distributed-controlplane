"""
risk_check.py - pre-trade risk gate.

Before an order fills (immediately, or later when a resting limit order
crosses - see routers/trading.py), check the most recent row the CRIMS-shaped
risk feed reported for that symbol. This is a real control derived from real
feed data flowing through the same tick pipeline the Alerts page already
reads - not a simulated/fabricated check - so an order is only blocked when
the same signal an operator would see elsewhere says BREACH.

Queries the RDB shard that owns the symbol directly (topology.shard_of -
the same partitioning bpipe_sim/crims_sim tag rows with, and the same
mapping the gateway itself routes by), NOT the gateway process. This was a
real, previously-shipped bug: gateway.q is a pure router with no `risk`
global of its own, so `select from risk where sym=...` sent to it has
always errored - confirmed live against a running cluster - and every call
here silently treated that as "checked, no breach found" instead of
recognizing it as an unreachable check. Making the fail-open/fail-closed
policy explicit (below) is what actually surfaced this: on fail-closed,
that error stopped being invisible and started blocking every order,
which is what led to finding it.

Fails CLOSED by default: if the risk feed can't be reached (query timeout,
gateway down), the order is blocked. "The feed didn't answer" is not
evidence the symbol is clean - it's an absence of evidence, and defaulting
to "let it through" turns a side-channel infra hiccup into an unverified
trade. RISK_GATE_FAIL_OPEN (config.Settings.risk_gate_fail_open) opts back
into the old behavior for desks that have consciously decided that tradeoff
- it's a policy switch, not a silent default, and every time it actually
lets a trade through unverified, the caller is expected to audit-log it
(see CheckResult.degraded below).
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from sqlmodel import Session, select

from . import market as mkt
from . import query_service as qs
from . import topology
from .config import Settings
from .models import DailyPnlBaseline, Position

log = logging.getLogger("risk_check")

_SHARD_COUNT = int(os.environ.get("SHARD_COUNT", "2"))
VOL_LOOKBACK_PRINTS = 200

_settings = Settings()


class RiskFeedUnreachable(Exception):
    """The risk feed could not be queried at all - distinct from "queried
    fine, no BREACH row found" so callers never conflate the two."""


def _connect_for_symbol(symbol: str):
    """Open a connection to the RDB shard that OWNS `symbol` - not the
    gateway, which has no `risk` table of its own (see module docstring)."""
    from qpython import qconnection
    shard_id = topology.shard_of(symbol, _SHARD_COUNT)
    shard = next(s for s in topology.shards(_SHARD_COUNT) if s.id == shard_id)
    host, port = topology.gateway_host(shard, "rdb").rsplit(":", 1)
    conn = qconnection.QConnection(host=host, port=int(port), pandas=False,
                                   timeout=int(os.environ.get("QUERY_TIMEOUT_SEC", "15")))
    conn.open()
    return conn


def latest_risk_row(symbol: str, connect: Callable[[str], object] = _connect_for_symbol) -> Optional[dict]:
    """Most recent risk row for `symbol` on the shard that owns it, or None
    if the risk feed has never reported on it. Raises RiskFeedUnreachable if
    the feed itself couldn't be queried - callers must not treat that the
    same as "checked, nothing found"."""
    query = f'select from risk where sym=`$"{symbol}"'
    conn = None
    try:
        conn = connect(symbol)
        grid = qs.run_query(query, conn, limit=qs.MAX_ROW_LIMIT)
    except Exception as exc:  # noqa: BLE001
        raise RiskFeedUnreachable(str(exc)) from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    rows = grid.get("rows") or []
    if not rows:
        return None
    cols = grid["columns"]
    time_i = cols.index("time")
    latest = max(rows, key=lambda r: r[time_i])
    return dict(zip(cols, latest))


@dataclass
class CheckResult:
    block_reason: Optional[str]  # None if the order may proceed
    degraded: bool = False       # True if the risk feed was unreachable - the
                                  # decision (either way) used the fail-open/
                                  # fail-closed policy, not a verified read.
                                  # Callers should audit-log a True here.
    realized_vol_annualized: Optional[float] = None  # informational - see
                                  # check_realized_volatility below


def _fetch_recent_prices(symbol: str, connect: Callable[[str], object]) -> list:
    """Last VOL_LOOKBACK_PRINTS trade prices for `symbol`, straight off the
    RDB shard that owns it - genuine recent prints, not a simulated series."""
    query = f'-{VOL_LOOKBACK_PRINTS}#select price from trade where sym=`$"{symbol}"'
    conn = None
    try:
        conn = connect(symbol)
        grid = qs.run_query(query, conn, limit=VOL_LOOKBACK_PRINTS)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    cols = grid.get("columns") or []
    if "price" not in cols:
        return []
    idx = cols.index("price")
    return [float(r[idx]) for r in grid.get("rows", []) if r[idx] is not None]


def check_realized_volatility(symbol: str, connect: Callable[[str], object] = _connect_for_symbol,
                              max_annualized: Optional[float] = None) -> CheckResult:
    """A SECOND, REAL check alongside the simulated CRIMS-style BREACH check
    below: realized volatility computed directly from `symbol`'s own live
    trade prints (app/market.py's summarize(), the same stat already shown
    elsewhere in the UI), not a fabricated signal. Opt-in
    (Settings.risk_max_realized_vol_annualized, 0 by default = not enforced)
    - a universal threshold doesn't exist across asset classes (crypto
    routinely runs far hotter than equities), so an unconfigured deployment
    gets this as informational context only, never a silent block. Any
    failure to reach live data here degrades to "not blocked" (not
    fail-closed like the CRIMS check) - this is a supplementary signal, and
    the CRIMS check above already provides the fail-closed backstop for
    "couldn't verify"."""
    if max_annualized is None:
        max_annualized = _settings.risk_max_realized_vol_annualized
    if not max_annualized:
        return CheckResult(block_reason=None)
    try:
        prices = _fetch_recent_prices(symbol, connect)
    except Exception as exc:  # noqa: BLE001
        log.warning("realized-volatility check unreachable for %s (not blocking on this "
                   "supplementary signal): %s", symbol, exc)
        return CheckResult(block_reason=None, degraded=True)
    if len(prices) < 3:
        return CheckResult(block_reason=None)
    vol = mkt.summarize(prices).get("realized_vol_annualized")
    if vol and vol > max_annualized:
        return CheckResult(
            block_reason=(
                f"pre-trade risk check failed: {symbol}'s realized volatility "
                f"({vol:.1%} annualized, from live trade prints) exceeds the configured "
                f"limit ({max_annualized:.1%})"
            ),
            realized_vol_annualized=vol,
        )
    return CheckResult(block_reason=None, realized_vol_annualized=vol)


def check_pretrade(symbol: str, connect: Callable[[str], object] = _connect_for_symbol,
                   fail_open: Optional[bool] = None) -> CheckResult:
    """CheckResult.block_reason is None if the order may proceed. `fail_open`
    defaults to the deployment's configured policy (Settings.risk_gate_fail_open,
    itself defaulting to False - fail closed) when not passed explicitly."""
    if fail_open is None:
        fail_open = _settings.risk_gate_fail_open
    try:
        row = latest_risk_row(symbol, connect=connect)
    except RiskFeedUnreachable as exc:
        if fail_open:
            log.warning("risk feed unreachable for %s, failing OPEN per policy: %s", symbol, exc)
            return CheckResult(block_reason=None, degraded=True)
        log.warning("risk feed unreachable for %s, failing CLOSED per policy: %s", symbol, exc)
        return CheckResult(
            block_reason=(
                f"pre-trade risk check could not be verified for {symbol} (risk feed "
                f"unreachable) - blocked by default; set RISK_GATE_FAIL_OPEN to change this policy"
            ),
            degraded=True,
        )
    if row is not None and row.get("status") == "BREACH":
        return CheckResult(block_reason=(
            f"pre-trade risk check failed: {symbol} is in BREACH on {row.get('riskType')} "
            f"(exposure {row.get('exposure')} vs limit {row.get('limit')})"
        ))
    # CRIMS-style check is clean (or has never reported on this symbol) - now
    # also run the real, live-data volatility check alongside it.
    return check_realized_volatility(symbol, connect=connect)


def check_portfolio_limits(tenant_id: int, symbol: str, side: str, qty: float, ref_price: float,
                           session: Session, settings: Optional[Settings] = None) -> CheckResult:
    """Portfolio-wide checks (daily loss, concentration) - distinct from
    check_pretrade above, which only ever looks at the ONE symbol being
    traded. Both opt-in (Settings.risk_max_daily_loss /
    risk_max_symbol_concentration_pct, 0 = not enforced) and both computed
    from Position rows already in the DB - no extra live-price round trip.

    Never blocks a trade that REDUCES exposure (a sell against an existing
    long, a buy against an existing short) - a limit breach should stop new
    risk from being added, not trap a desk unable to flatten a position
    that's already over the line. Only position-opening/-growing trades can
    be blocked here."""
    settings = settings or Settings()
    if not settings.risk_max_daily_loss and not settings.risk_max_symbol_concentration_pct:
        return CheckResult(block_reason=None)

    positions = session.exec(select(Position).where(Position.tenant_id == tenant_id)).all()
    existing = next((p for p in positions if p.symbol == symbol), None)
    is_reducing = existing is not None and existing.qty != 0 and (
        (side.lower() == "sell" and existing.qty > 0) or (side.lower() == "buy" and existing.qty < 0)
    )
    if is_reducing:
        return CheckResult(block_reason=None)

    if settings.risk_max_daily_loss:
        today = datetime.utcnow().date()
        baseline = session.exec(select(DailyPnlBaseline).where(
            DailyPnlBaseline.tenant_id == tenant_id, DailyPnlBaseline.trading_date == today)).first()
        total_realized = sum(p.realized_pnl for p in positions)
        if baseline is None:
            baseline = DailyPnlBaseline(tenant_id=tenant_id, trading_date=today,
                                        baseline_realized_pnl=total_realized)
            session.add(baseline)
            session.commit()
        today_pnl = total_realized - baseline.baseline_realized_pnl
        if today_pnl < -abs(settings.risk_max_daily_loss):
            return CheckResult(block_reason=(
                f"pre-trade risk check failed: today's realized P&L ({today_pnl:.2f}) has "
                f"already breached the configured daily loss limit "
                f"(-{abs(settings.risk_max_daily_loss):.2f}) - only position-reducing trades "
                f"are allowed for the rest of the day"
            ))

    if settings.risk_max_symbol_concentration_pct:
        notional_by_symbol = {p.symbol: abs(p.qty * p.avg_price) for p in positions}
        existing_notional = notional_by_symbol.pop(symbol, 0.0)
        new_notional = existing_notional + abs(qty * ref_price)
        total_notional = sum(notional_by_symbol.values()) + new_notional
        if total_notional > 0:
            pct = new_notional / total_notional
            if pct > settings.risk_max_symbol_concentration_pct:
                return CheckResult(block_reason=(
                    f"pre-trade risk check failed: this trade would put {pct:.0%} of "
                    f"portfolio notional in {symbol}, above the configured concentration "
                    f"limit ({settings.risk_max_symbol_concentration_pct:.0%})"
                ))

    return CheckResult(block_reason=None)
