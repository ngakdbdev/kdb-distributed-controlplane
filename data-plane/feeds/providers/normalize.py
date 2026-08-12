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

from .base import Tick, iso_to_dt, ms_to_dt, sec_to_dt


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


_SIDE = {"buy": "B", "sell": "S"}


def _crypto_size(qty) -> int:
    """The shared `trade` schema types `size` as a q long (schema.q) - built
    for whole-share equity counts. Crypto quantities are fractional (0.01
    BTC is a completely ordinary trade size), so naively truncating with
    int() would silently zero out most real crypto trades. There's no lot-
    size/contract-multiplier concept in this schema to rescale by
    consistently, so this is a genuine, disclosed precision loss - not
    something a feed adapter should invent a fix for on its own - rounding
    to the nearest whole unit is the least-surprising choice available
    without a schema change (a size column of a different type/precision
    for crypto rows specifically, tracked as a real follow-on)."""
    try:
        return max(0, round(float(qty or 0)))
    except (TypeError, ValueError):
        return 0


def coinbase_match(msg: dict) -> list:
    """Coinbase Exchange public feed, "matches" channel: {"type":"match",
    "product_id","price","size","side","time"(ISO8601),...}. `side` is the
    TAKER's side (the aggressor) - "sell" means a sell order hit the book.
    No API key needed; this is the public market-data feed, not the
    authenticated trading one."""
    if not isinstance(msg, dict) or msg.get("type") not in ("match", "last_match"):
        return []
    sym = msg.get("product_id")
    price = msg.get("price")
    if sym is None or price is None:
        return []
    try:
        price = float(price)
    except (TypeError, ValueError):
        return []
    return [Tick(symbol=sym, price=price, size=_crypto_size(msg.get("size")),
                 side=_SIDE.get(msg.get("side", ""), ""),
                 venue="coinbase", ts=iso_to_dt(msg.get("time")))]


def kraken_trade(msg: dict) -> list:
    """Kraken WebSocket API v2, "trade" channel: {"channel":"trade",
    "type":"update"|"snapshot","data":[{"symbol","side","price","qty",
    "timestamp"(ISO8601),...}]}. No API key needed - public market data."""
    if not isinstance(msg, dict) or msg.get("channel") != "trade":
        return []
    out = []
    for d in msg.get("data") or []:
        sym = d.get("symbol")
        price = d.get("price")
        if sym is None or price is None:
            continue
        out.append(Tick(symbol=sym, price=float(price), size=_crypto_size(d.get("qty")),
                        side=_SIDE.get(d.get("side", ""), ""),
                        venue="kraken", ts=iso_to_dt(d.get("timestamp"))))
    return out


def binance_trade(msg: dict) -> list:
    """Binance combined-stream public trade endpoint (wss://stream.binance.com:9443/
    stream?streams=<sym>@trade/...): {"stream":"<sym>@trade","data":{"e":"trade",
    "s","p","q","T"(ms),"m":bool,...}}. No API key needed - public market data.
    `m` is whether the BUYER was the maker; the taker (aggressor) side is the
    OPPOSITE - m=true means a sell hit the book (the seller was the aggressor)."""
    if not isinstance(msg, dict):
        return []
    d = msg.get("data") if isinstance(msg.get("data"), dict) else msg  # combined- or raw single-stream frame
    if not isinstance(d, dict) or d.get("e") != "trade":
        return []
    sym = d.get("s")
    price = d.get("p")
    if sym is None or price is None:
        return []
    try:
        price = float(price)
    except (TypeError, ValueError):
        return []
    return [Tick(symbol=sym, price=price, size=_crypto_size(d.get("q")),
                 side="S" if d.get("m") else "B",
                 venue="binance", ts=ms_to_dt(d.get("T")))]


def binance_depth(msg: dict) -> list:
    """Binance combined-stream partial book depth endpoint (<sym>@depth@100ms):
    {"stream":"<sym>@depth@100ms","data":{"e":"depthUpdate","s":"BTCUSDT",
    "E"(ms),"b":[[price,qty],...],"a":[[price,qty],...]}}. No API key needed.

    This is L2 ORDER BOOK data, not trade prints - every bid/ask price-level
    change is its own row here (side="BID"/"ASK", never "B"/"S" - those mean
    an executed trade's aggressor side and must never be confused with a
    quoted level). Published into the SAME `trade` table every other feed
    uses (reusing the existing schema rather than adding a new `book` table
    across every q process) - venue="binance-depth" (never plain "binance")
    makes these unambiguous: `price`/`size` here mean a QUOTED level, not a
    fill. A qty of "0" means the level was REMOVED - still published as a
    real event (size=0 at that price), not dropped, since a level clearing
    out is itself real book activity."""
    if not isinstance(msg, dict):
        return []
    d = msg.get("data") if isinstance(msg.get("data"), dict) else msg
    if not isinstance(d, dict) or d.get("e") != "depthUpdate":
        return []
    sym = d.get("s")
    if sym is None:
        return []
    ts = ms_to_dt(d.get("E"))
    out = []
    for side, levels in (("BID", d.get("b") or []), ("ASK", d.get("a") or [])):
        for level in levels:
            try:
                price, qty = level
                out.append(Tick(symbol=sym, price=float(price), size=_crypto_size(qty),
                                side=side, venue="binance-depth", ts=ts))
            except (TypeError, ValueError):
                continue
    return out


def bybit_trade(msg: dict) -> list:
    """Bybit v5 public spot feed, "publicTrade.<SYM>" topic:
    {"topic":"publicTrade.BTCUSDT","data":[{"s","S"("Buy"/"Sell"),"p","v",
    "T"(ms)},...]}. No API key needed - public market data."""
    if not isinstance(msg, dict) or not str(msg.get("topic", "")).startswith("publicTrade."):
        return []
    out = []
    for d in msg.get("data") or []:
        sym = d.get("s")
        price = d.get("p")
        if sym is None or price is None:
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        out.append(Tick(symbol=sym, price=price, size=_crypto_size(d.get("v")),
                        side=_SIDE.get(str(d.get("S", "")).lower(), ""),
                        venue="bybit", ts=ms_to_dt(d.get("T"))))
    return out


def okx_trade(msg: dict) -> list:
    """OKX v5 public "trades" channel: {"arg":{"channel":"trades","instId":...},
    "data":[{"instId","px","sz","side"("buy"/"sell"),"ts"(ms, string)},...]}.
    No API key needed - public market data."""
    if not isinstance(msg, dict) or (msg.get("arg") or {}).get("channel") != "trades":
        return []
    out = []
    for d in msg.get("data") or []:
        sym = d.get("instId")
        price = d.get("px")
        if sym is None or price is None:
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        out.append(Tick(symbol=sym, price=price, size=_crypto_size(d.get("sz")),
                        side=_SIDE.get(d.get("side", ""), ""),
                        venue="okx", ts=ms_to_dt(d.get("ts"))))
    return out
