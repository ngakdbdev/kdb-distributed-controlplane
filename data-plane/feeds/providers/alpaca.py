"""
alpaca.py - LIVE adapter for Alpaca's market-data v2 stream (US equities/ETFs).

wss://stream.data.alpaca.markets/v2/{feed} - "feed" is "iex" (free tier,
IEX-only trades, what this defaults to) or "sip" (consolidated tape, needs a
paid Alpaca subscription; override with ALPACA_DATA_FEED). Auth is a JSON
message after connect (not a URL token like Finnhub), then a symbol
subscribe message - see run() below for the exact handshake.

This is data only. Alpaca is also a broker - see control-api/app/alpaca_broker.py
for the (separately gated, off-by-default) order-placement side. The two
share credentials (ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY) but are otherwise
unrelated: this module never places an order, and alpaca_broker.py never
touches market-data ingestion.
"""
from __future__ import annotations

import json
import os

from .base import MarketDataProvider, ProviderError, http_get_json
from . import normalize


class AlpacaProvider(MarketDataProvider):
    name = "alpaca"
    display_name = "Alpaca"
    live = True
    coverage = "US equities/ETFs - real-time IEX trades free, consolidated SIP tape on a paid plan"
    requires = "a free Alpaca account's API key ID + secret key (ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY) - the same credentials used for paper trading"

    WS_URL = "wss://stream.data.alpaca.markets/v2/{feed}"
    # Asset reference data (fetch_all_symbols below) lives on the TRADING
    # API, not the market-data WS_URL above - and, like alpaca_broker.py's
    # own paper/live split, a paper key is only accepted by the paper base
    # and a live key only by the live one.
    ASSETS_PAPER_BASE = "https://paper-api.alpaca.markets"
    ASSETS_LIVE_BASE = "https://api.alpaca.markets"

    def __init__(self, *args, api_secret: str = "", data_feed: str = "iex", **kwargs):
        super().__init__(*args, **kwargs)
        self.api_secret = api_secret
        self.data_feed = data_feed or "iex"

    @staticmethod
    def fetch_all_symbols(fetch=None) -> list:
        """Every currently active, tradable US-equity/ETF symbol Alpaca
        lists, straight from its own /v2/assets - not a hardcoded list that
        drifts stale the moment something is added/delisted/halted, same
        idea as the crypto providers' own fetch_all_symbols against THEIR
        venue's real instrument list. Unlike those (public endpoints), this
        one needs auth - and runner.py's `--symbols all` calls this with no
        provider instance yet to read credentials off of, so they come
        straight from the environment, the same two variables run() itself
        needs.

        Realistic expectation, not a footnote to skip past: this can come
        back with several thousand symbols (Alpaca's full active US-equity
        universe), most of which rarely print a trade at all. This exists
        so `--symbols all` (or a symbols file built from it) can pull the
        REAL current list to filter down from - it does not mean every
        deployment should actually subscribe to the whole thing verbatim.
        """
        base = (AlpacaProvider.ASSETS_LIVE_BASE
                if os.environ.get("ALPACA_TRADING_MODE", "").strip().lower() == "live"
                else AlpacaProvider.ASSETS_PAPER_BASE)
        url = f"{base}/v2/assets?status=active&asset_class=us_equity"
        if fetch is None:
            key = os.environ.get("ALPACA_API_KEY_ID", "")
            secret = os.environ.get("ALPACA_API_SECRET_KEY", "")
            if not key or not secret:
                raise ProviderError(
                    "alpaca needs both ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY set in the "
                    "environment to fetch its full symbol universe (the assets list is an "
                    "authenticated endpoint, unlike the crypto venues' public instrument lists)")
            fetch = lambda u: http_get_json(u, headers={  # noqa: E731
                "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        data = fetch(url)
        return sorted({r["symbol"] for r in data
                       if isinstance(r, dict) and r.get("tradable") and r.get("symbol")})

    def _handle_raw(self, raw: str) -> int:
        """Parse one websocket frame (a JSON ARRAY of events - Alpaca batches
        trade/quote/bar/status messages together) and publish. Split out from
        the socket loop so it's unit-testable with a fake publisher and no
        network."""
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return 0
        return self._publish(normalize.alpaca_trade(msg))

    def run(self) -> None:
        if not self.api_key or not self.api_secret:
            raise ProviderError(
                "alpaca needs both an API key ID and secret key "
                "(ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY) - a free Alpaca account has both"
            )
        import websocket  # lazy: websocket-client, only needed to actually run

        # Mutable box so on_open/on_message (two separate closures) share one
        # flag - MUST be reset on every on_open, not just declared once
        # outside it. Confirmed live: with a module-level `authed = {"done":
        # False}` declared once before ws.run_forever(), this stayed True
        # forever after the FIRST successful auth - run_forever's automatic
        # reconnect (below) calls on_open again on every reconnect (and does
        # send a fresh auth frame), but on_message's `if not authed["done"]`
        # subscribe-gate never re-armed, so every reconnect after the first
        # silently re-authenticated and then never resubscribed to anything.
        # Over a 2-day run with ~160 reconnects (Alpaca's free IEX stream
        # drops idle/long-lived connections often), that meant real trade
        # data flowed for the first ~90 minutes and then nothing, with
        # nothing in the logs to suggest a problem (no errors - just an
        # authenticated, subscribed-to-nothing connection sitting there).
        authed = {"done": False}

        def on_open(ws):
            authed["done"] = False
            ws.send(json.dumps({"action": "auth", "key": self.api_key, "secret": self.api_secret}))
            # subscribe is sent from on_message once auth succeeds (see below) -
            # sending it immediately after auth, before Alpaca has confirmed
            # the auth frame, is a documented race in their own client
            # examples that silently drops the subscribe.

        def on_message(ws, raw):
            if not authed["done"]:
                try:
                    events = json.loads(raw)
                except (ValueError, TypeError):
                    return
                events = events if isinstance(events, list) else [events]
                for e in events:
                    if isinstance(e, dict) and e.get("T") == "success" and e.get("msg") == "authenticated":
                        authed["done"] = True
                        ws.send(json.dumps({"action": "subscribe", "trades": self.symbols}))
                        self.log.info("authenticated, subscribed to %d symbols", len(self.symbols))
                    elif isinstance(e, dict) and e.get("T") == "error":
                        self.log.warning("alpaca stream error: %s", e.get("msg"))
                return
            self._handle_raw(raw)

        def on_error(ws, err):
            self.log.warning("alpaca ws error: %s", err)

        ws = websocket.WebSocketApp(self.WS_URL.format(feed=self.data_feed),
                                    on_open=on_open, on_message=on_message,
                                    on_error=on_error)
        ws.run_forever(reconnect=5)
