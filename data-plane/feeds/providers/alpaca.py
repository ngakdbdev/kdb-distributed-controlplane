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

from .base import MarketDataProvider, ProviderError
from . import normalize


class AlpacaProvider(MarketDataProvider):
    name = "alpaca"
    display_name = "Alpaca"
    live = True
    coverage = "US equities/ETFs - real-time IEX trades free, consolidated SIP tape on a paid plan"
    requires = "a free Alpaca account's API key ID + secret key (ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY) - the same credentials used for paper trading"

    WS_URL = "wss://stream.data.alpaca.markets/v2/{feed}"

    def __init__(self, *args, api_secret: str = "", data_feed: str = "iex", **kwargs):
        super().__init__(*args, **kwargs)
        self.api_secret = api_secret
        self.data_feed = data_feed or "iex"

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

        def on_open(ws):
            ws.send(json.dumps({"action": "auth", "key": self.api_key, "secret": self.api_secret}))
            # subscribe is sent from on_message once auth succeeds (see below) -
            # sending it immediately after auth, before Alpaca has confirmed
            # the auth frame, is a documented race in their own client
            # examples that silently drops the subscribe.

        authed = {"done": False}

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
