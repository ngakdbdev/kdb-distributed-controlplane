"""
normalize.py - pure functions turning each vendor's websocket payload into a
list of canonical Ticks. No sockets, no publishing, no topology: this is the
part that has to be exactly right (parsing real vendor formats), so it's the
part that's unit-tested in providers/tests/test_normalize.py.

Field references are the documented websocket shapes at time of writing; vendor
formats drift, so if a live feed produces nothing, check the current docs and
adjust here - the adapters call straight through to these.
"""
from __future__ import annotations

from .base import Tick, ms_to_dt, sec_to_dt


def finnhub_trades(msg: dict) -> list:
    """Finnhub: {"type":"trade","data":[{"s","p","v","t"(ms),...}]}.
    Finnhub trade messages don't carry aggressor side."""
    if not isinstance(msg, dict) or msg.get("type") != "trade":
        return []
    out = []
    for d in msg.get("data") or []:
        sym = d.get("s")
        price = d.get("p")
        if sym is None or price is None:
            continue
        out.append(Tick(symbol=sym, price=float(price),
                        size=int(d.get("v", 0) or 0), side="",
                        venue="finnhub", ts=ms_to_dt(d.get("t"))))
    return out


def twelvedata_price(msg: dict) -> list:
    """Twelve Data: {"event":"price","symbol","price","timestamp"(s),
    "exchange",...}. One tick per message."""
    if not isinstance(msg, dict) or msg.get("event") != "price":
        return []
    sym = msg.get("symbol")
    price = msg.get("price")
    if sym is None or price is None:
        return []
    return [Tick(symbol=sym, price=float(price),
                 size=int(msg.get("day_volume", 0) or 0), side="",
                 venue=msg.get("exchange", "") or "",
                 ts=sec_to_dt(msg.get("timestamp")))]


def polygon_messages(msg) -> list:
    """Polygon: an array of events; trades are {"ev":"T","sym","p","s","t"(ms),
    "x"(exchange id)}. Status/other events are ignored."""
    events = msg if isinstance(msg, list) else [msg]
    out = []
    for e in events:
        if not isinstance(e, dict) or e.get("ev") != "T":
            continue
        sym = e.get("sym")
        price = e.get("p")
        if sym is None or price is None:
            continue
        venue = e.get("x")
        out.append(Tick(symbol=sym, price=float(price),
                        size=int(e.get("s", 0) or 0), side="",
                        venue=str(venue) if venue is not None else "",
                        ts=ms_to_dt(e.get("t"))))
    return out


def yahoo_quotes(data: dict) -> list:
    """Yahoo (UNOFFICIAL) quote endpoint: {"quoteResponse":{"result":[
    {"symbol","regularMarketPrice","regularMarketVolume","fullExchangeName",
     "regularMarketTime"(sec)},...]}}. Delayed quotes, polled - not trades."""
    if not isinstance(data, dict):
        return []
    result = (data.get("quoteResponse") or {}).get("result") or []
    out = []
    for r in result:
        sym = r.get("symbol")
        price = r.get("regularMarketPrice")
        if sym is None or price is None:
            continue
        out.append(Tick(symbol=sym, price=float(price),
                        size=int(r.get("regularMarketVolume", 0) or 0), side="",
                        venue=r.get("fullExchangeName", "") or "",
                        ts=sec_to_dt(r.get("regularMarketTime"))))
    return out


def alphavantage_quote(data: dict) -> list:
    """Alpha Vantage GLOBAL_QUOTE: {"Global Quote":{"01. symbol","05. price",
    "06. volume",...}}. One symbol per call (free tier is heavily rate-limited)."""
    if not isinstance(data, dict):
        return []
    q = data.get("Global Quote") or data.get("globalQuote") or {}
    sym = q.get("01. symbol") or q.get("symbol")
    price = q.get("05. price") or q.get("price")
    if not sym or price is None:
        return []
    try:
        price = float(price)
    except (TypeError, ValueError):
        return []
    return [Tick(symbol=sym, price=price,
                 size=int(float(q.get("06. volume", 0) or 0)), side="",
                 venue="", ts=None)]
