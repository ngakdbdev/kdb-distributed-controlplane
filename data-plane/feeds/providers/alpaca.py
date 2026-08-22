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

SYMBOL COUNT: the free/IEX stream has a real, enforced-but-undocumented cap
on simultaneous subscribed symbols. Confirmed live: 13,393 symbols (fetch_
all_symbols()'s full universe) AND 68 symbols were both rejected outright
with {"T":"error","code":405,"msg":"symbol limit exceeded"}; 30 symbols
was accepted and real trade data flowed within seconds. The true ceiling
sits somewhere in (30, 68], not narrowed further than that. ALPACA_SYMBOLS
should stay a curated, bounded list (or a symbols file) - "all" WILL
authenticate cleanly and log "subscribed" with no error visible unless
on_message's unconditional error-frame check (see run() below) is intact,
which is exactly what made this take days to diagnose the first time: the
error frame arrives as the very next message after auth succeeds, and a
prior version of this code only checked for "T":"error" pre-auth, so the
one message that explained everything was silently discarded as "not a
trade event" instead of ever being logged.
"""
from __future__ import annotations

import json
import os
import time

from .base import MarketDataProvider, ProviderError, chunked, http_get_json
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
    # Same reasoning as coinbase.py's own SUBSCRIBE_CHUNK: batch the
    # "subscribe" frame instead of sending every symbol in one message.
    # Confirmed live: with --symbols all (13,377 symbols), a single
    # unchunked {"action":"subscribe","trades":[...]} frame authenticated
    # fine (auth is a separate, small message) but silently never resulted
    # in any trade data at all - no error frame from Alpaca either, the
    # connection just sat there authenticated and quiet. The client-side
    # "subscribed" log fired right after ws.send() returned, which only
    # proves the frame was SENT, never that Alpaca actually accepted it -
    # a large single subscribe message being silently dropped is
    # consistent with every symptom actually observed (zero alpaca-venue
    # rows across a full trading day despite a "clean" subscribe log).
    SUBSCRIBE_CHUNK = 200

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
            # Error frames (e.g. {"T":"error","code":405,"msg":"symbol limit
            # exceeded"}) can arrive at ANY point, not just pre-auth -
            # confirmed live: Alpaca sends this immediately after the
            # subscribe request, i.e. the very next message after auth
            # succeeds. This check MUST run unconditionally, before the
            # authed["done"] gate below - a prior version only checked for
            # "T":"error" inside the pre-auth branch, so once authed["done"]
            # flipped True this exact error was routed straight to
            # _handle_raw instead (silently discarded as "not a trade
            # event"), and never appeared in any log. That's the real reason
            # zero trade rows ever landed regardless of symbol count or
            # connection stability - Alpaca was rejecting the subscription
            # outright, every time, and nothing ever said so.
            try:
                events = json.loads(raw)
            except (ValueError, TypeError):
                return
            events_list = events if isinstance(events, list) else [events]
            for e in events_list:
                if isinstance(e, dict) and e.get("T") == "error":
                    self.log.warning("alpaca stream error (code %s): %s", e.get("code"), e.get("msg"))

            if not authed["done"]:
                for e in events_list:
                    if isinstance(e, dict) and e.get("T") == "success" and e.get("msg") == "authenticated":
                        authed["done"] = True
                        for batch in chunked(self.symbols, self.SUBSCRIBE_CHUNK):
                            ws.send(json.dumps({"action": "subscribe", "trades": batch}))
                            time.sleep(0.1)
                        self.log.info("authenticated, subscribed to %d symbols in batches of %d",
                                      len(self.symbols), self.SUBSCRIBE_CHUNK)
                return
            self._handle_raw(raw)

        def on_error(ws, err):
            self.log.warning("alpaca ws error: %s", err)

        ws = websocket.WebSocketApp(self.WS_URL.format(feed=self.data_feed),
                                    on_open=on_open, on_message=on_message,
                                    on_error=on_error)
        ws.run_forever(reconnect=5)
