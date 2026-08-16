"""
alpaca_broker.py - order execution through a real Alpaca brokerage account.

Two modes, both requiring ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY (a free
Alpaca account has both - the same pair data-plane/feeds/providers/alpaca.py
uses for market data, though that module never places an order and this one
never touches market-data ingestion):

  * paper (ALPACA_TRADING_MODE=paper) - https://paper-api.alpaca.markets.
    A REAL broker-side order simulation running against Alpaca's own live
    market data - real order types, real partial-fill/rejection behavior -
    but no real money ever moves. This is a genuine upgrade in realism over
    this codebase's own PaperRouter (a bare ref-price fill), with the same
    zero-risk guarantee.

  * live (ALPACA_TRADING_MODE=live) - https://api.alpaca.markets. REAL
    MONEY moves. See oms.py's BrokerRouter docstring for why this codebase
    has never shipped a live-order path before now: "live order routing is
    regulated activity that needs broker connectivity, entitlements, and
    compliance sign-off" - true whether the adapter is Alpaca or anyone
    else. For that reason "live" additionally requires a SECOND, exact-
    string environment variable (ALPACA_LIVE_TRADING_ACK) before this
    module will ever touch the live endpoint - see trading_mode() below.
    One mistyped/copy-pasted ALPACA_TRADING_MODE=live cannot enable
    real-money trading by accident; it takes two deliberate, unrelated
    values both being set correctly. Going live on a product shipped to
    customers (this one is) is a business/legal decision that needs actual
    compliance review - this file makes it possible to configure once
    that's been done, not easier to stumble into before it has.

Both modes go through the SAME pre-trade risk gate every other order does
(routers/trading.py's _check_pretrade_audited, via oms.AlpacaRouter) - there
is no path, paper or live, that skips it.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional


class AlpacaError(RuntimeError):
    pass


PAPER_BASE_URL = "https://paper-api.alpaca.markets"
LIVE_BASE_URL = "https://api.alpaca.markets"

# The exact value ALPACA_LIVE_TRADING_ACK must equal, in addition to
# ALPACA_TRADING_MODE=live, before this module will ever construct a live
# client. Deliberately not a plain "true"/"1" - those are too easy to set by
# accident (a stray copy-pasted .env line, a templating bug). Whoever sets
# this is expected to have read the module docstring above first.
LIVE_ACK_PHRASE = "I_UNDERSTAND_THIS_TRADES_REAL_MONEY"


def trading_mode() -> str:
    """'off' (default - no env vars set) | 'paper' | 'live'.

    'live' silently DOWNGRADES to 'paper' if ALPACA_LIVE_TRADING_ACK doesn't
    exactly match LIVE_ACK_PHRASE, rather than raising - so a bot loop
    calling this can never end up MORE dangerous than what was actually,
    deliberately configured; the failure mode of a missing/wrong ack is
    always "stayed safe", never "traded real money anyway"."""
    mode = os.environ.get("ALPACA_TRADING_MODE", "off").strip().lower()
    if mode not in ("off", "paper", "live"):
        return "off"
    if mode == "live" and os.environ.get("ALPACA_LIVE_TRADING_ACK", "") != LIVE_ACK_PHRASE:
        return "paper"
    return mode


class AlpacaClient:
    def __init__(self, key_id: str, secret_key: str, live: bool = False):
        self.key_id = key_id
        self.secret_key = secret_key
        self.live = live
        self.base_url = LIVE_BASE_URL if live else PAPER_BASE_URL

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise AlpacaError(f"Alpaca {method} {path} -> HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise AlpacaError(f"Alpaca {method} {path} unreachable: {exc.reason}") from exc

    def account(self) -> dict:
        return self._request("GET", "/v2/account")

    def list_positions(self) -> list:
        return self._request("GET", "/v2/positions")

    def submit_order(self, symbol: str, side: str, qty: float, order_type: str = "market",
                     limit_price: Optional[float] = None, time_in_force: str = "day") -> dict:
        body = {"symbol": symbol, "side": side.lower(), "qty": str(qty),
                "type": order_type, "time_in_force": time_in_force}
        if order_type == "limit":
            if limit_price is None:
                raise AlpacaError("limit order needs a limit_price")
            body["limit_price"] = str(limit_price)
        return self._request("POST", "/v2/orders", body)

    def get_order(self, order_id: str) -> dict:
        return self._request("GET", f"/v2/orders/{order_id}")

    def is_tradable(self, symbol: str) -> bool:
        """True if Alpaca lists this symbol as an active, tradable asset.
        Confirmed live: a bot trading a symbol from this platform's own
        demo/simulated feed universe (e.g. a synthetic crypto-style ticker
        no real exchange lists) had every single order attempt rejected
        with 422 "asset not found" - not a transient error, so callers
        should check this BEFORE calling submit_order for a new symbol,
        not just handle the rejection after the fact."""
        try:
            asset = self._request("GET", f"/v2/assets/{symbol}")
        except AlpacaError:
            return False
        return bool(asset.get("tradable")) and asset.get("status") == "active"

    def wait_for_fill(self, order_id: str, timeout_sec: float = 10.0, poll_sec: float = 0.5) -> dict:
        """Alpaca order placement is asynchronous (new -> accepted -> filled),
        unlike this codebase's own PaperRouter's instant ref-price fill. A
        market order on a liquid symbol during market hours typically fills
        in well under a second, even in Alpaca's paper environment - poll
        briefly rather than assume the placement response IS the fill.
        Raises on timeout, rejection, cancellation, or expiry; callers must
        never treat "no exception raised yet" as "definitely filled" without
        this - the whole point of calling it is to not have to."""
        deadline = time.monotonic() + timeout_sec
        last = None
        while time.monotonic() < deadline:
            last = self.get_order(order_id)
            status = last.get("status")
            if status == "filled":
                return last
            if status in ("rejected", "canceled", "expired"):
                raise AlpacaError(
                    f"order {order_id} ended as '{status}'"
                    + (f": {last.get('reject_reason')}" if last.get("reject_reason") else ""))
            time.sleep(poll_sec)
        raise AlpacaError(
            f"order {order_id} did not fill within {timeout_sec}s "
            f"(last known status: {(last or {}).get('status', 'unknown')})")


def client_from_env() -> Optional[AlpacaClient]:
    """None if trading_mode() is 'off', or credentials are missing - callers
    (oms.py's router selection) must treat None as "fall back to the
    internal PaperRouter", never raise. This is the ONLY function anything
    outside this module should call to get a client - it's what actually
    enforces trading_mode()'s live/paper decision, rather than leaving that
    up to whoever constructs an AlpacaClient directly."""
    mode = trading_mode()
    if mode == "off":
        return None
    key_id = os.environ.get("ALPACA_API_KEY_ID", "")
    secret_key = os.environ.get("ALPACA_API_SECRET_KEY", "")
    if not key_id or not secret_key:
        return None
    return AlpacaClient(key_id, secret_key, live=(mode == "live"))


# How long a broker-tradability check is trusted before re-checking - an
# asset's tradable status doesn't change minute to minute. Shared by both
# the auto-mode bot (signal_engine.py, which polls every ~12s and would
# otherwise re-check/re-attempt a doomed order every single poll) and
# manual order placement (oms.AlpacaRouter.fill, below) - one cache, one
# TTL, not two independently-drifting copies of the same idea.
_TRADABILITY_TTL_SEC = 3600
_tradability_cache: dict[str, tuple[bool, float]] = {}


def cached_is_tradable(symbol: str) -> bool:
    """True if there's no real broker configured to check against
    (PaperRouter/no client - accepts any symbol) or the configured Alpaca
    account confirms it lists this one. Confirmed live: this platform's
    live-discovered symbol universe (see app/symbol_discovery.py) includes
    plenty of pairs (crypto cross-rates like BTC-GBP, symbols from
    simulated/demo feeds) that a real feed happily prints trades for but
    that Alpaca has never heard of - without this check, an order for one
    of those reaches Alpaca's API and comes back a raw HTTP 422 with
    Alpaca's own JSON error text, which is both confusing to a trader and,
    for the auto-bot, a wasted doomed order attempt every poll forever."""
    client = client_from_env()
    if client is None:
        return True
    now = time.monotonic()
    cached = _tradability_cache.get(symbol)
    if cached is not None and (now - cached[1]) < _TRADABILITY_TTL_SEC:
        return cached[0]
    tradable = client.is_tradable(symbol)
    _tradability_cache[symbol] = (tradable, now)
    return tradable
