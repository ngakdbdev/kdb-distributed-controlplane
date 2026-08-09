"""
risk_check.py - pre-trade risk gate.

Before an order fills (immediately, or later when a resting limit order
crosses - see routers/trading.py), check the most recent row the CRIMS-shaped
risk feed reported for that symbol. This is a real control derived from real
feed data flowing through the same tick pipeline the Alerts page already
reads - not a simulated/fabricated check - so an order is only blocked when
the same signal an operator would see elsewhere says BREACH.

Fails OPEN: if the risk feed can't be reached (query timeout, gateway down),
this does not block trading - an infra hiccup on a side channel isn't a
reason to halt the desk. It only blocks on an actual, successfully-read
BREACH row.
"""
import logging
import os
from typing import Optional

from . import query_service as qs

log = logging.getLogger("risk_check")

_GW_HOST = os.environ.get("QUERY_GATEWAY_HOST", "gateway")
_GW_PORT = int(os.environ.get("QUERY_GATEWAY_PORT", "5050"))


def _connect():
    from qpython import qconnection
    conn = qconnection.QConnection(host=_GW_HOST, port=_GW_PORT, pandas=False,
                                   timeout=int(os.environ.get("QUERY_TIMEOUT_SEC", "15")))
    conn.open()
    return conn


def latest_risk_row(symbol: str, connect=_connect) -> Optional[dict]:
    """Most recent risk row for `symbol` (across shards), or None if the risk
    feed has never reported on it or couldn't be reached."""
    query = f'select from risk where sym=`$"{symbol}"'
    conn = None
    try:
        conn = connect()
        grid = qs.run_query(query, conn, limit=qs.MAX_ROW_LIMIT)
    except Exception as exc:  # noqa: BLE001 - fail open, see module docstring
        log.warning("risk feed unreachable for %s, failing open: %s", symbol, exc)
        return None
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


def check_pretrade(symbol: str, connect=_connect) -> Optional[str]:
    """None if the order may proceed; otherwise a human-readable block reason."""
    row = latest_risk_row(symbol, connect=connect)
    if row is None or row.get("status") != "BREACH":
        return None
    return (f"pre-trade risk check failed: {symbol} is in BREACH on {row.get('riskType')} "
            f"(exposure {row.get('exposure')} vs limit {row.get('limit')})")
