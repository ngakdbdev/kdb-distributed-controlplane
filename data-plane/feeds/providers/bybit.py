"""
bybit.py - LIVE adapter. wss://stream.bybit.com/v5/public/spot
Bybit's public v5 "publicTrade" topic - real trades, no API key or account
needed. Symbols are Bybit's own concatenated pairs (uppercase, e.g. BTCUSDT).

fetch_all_symbols() pulls Bybit's own live instrument list (GET
/v5/market/instruments-info?category=spot) - the real, current tradable
universe, not a hardcoded guess. Subscriptions are batched (SUBSCRIBE_CHUNK
topics per "subscribe" frame, several frames on the SAME connection) rather
than one giant message, matching Bybit's own per-request args guidance.
"""
from __future__ import annotations

import json

from .base import MarketDataProvider, chunked, http_get_json
from . import normalize

INSTRUMENTS_URL = "https://api.bybit.com/v5/market/instruments-info?category=spot"


class BybitProvider(MarketDataProvider):
    name = "bybit"
    display_name = "Bybit"
    live = True
    coverage = "crypto spot - real-time trades, no key required"
    requires = "nothing - public feed (symbols in Bybit's own pair format, e.g. BTCUSDT)"

    WS_URL = "wss://stream.bybit.com/v5/public/spot"
    SUBSCRIBE_CHUNK = 200

    @staticmethod
    def fetch_all_symbols(fetch=http_get_json) -> list:
        """Every currently-Trading spot pair, straight from Bybit's own
        instrument list."""
        data = fetch(INSTRUMENTS_URL)
        rows = (data.get("result") or {}).get("list") or []
        return [r["symbol"] for r in rows if r.get("status") == "Trading"]

    def _handle_raw(self, raw: str) -> int:
        """Parse one websocket frame and publish. Split out from the socket
        loop so it's unit-testable with a fake publisher and no network."""
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return 0
        return self._publish(normalize.bybit_trade(msg))

    def run(self) -> None:
        import websocket  # lazy: websocket-client, only needed to actually run

        def on_open(ws):
            topics = [f"publicTrade.{s.upper()}" for s in self.symbols]
            for batch in chunked(topics, self.SUBSCRIBE_CHUNK):
                ws.send(json.dumps({"op": "subscribe", "args": batch}))
            self.log.info("subscribed to %d symbols (%d batches)", len(self.symbols),
                         -(-len(topics) // self.SUBSCRIBE_CHUNK))

        def on_message(ws, raw):
            self._handle_raw(raw)

        def on_error(ws, err):
            self.log.warning("bybit ws error: %s", err)

        ws = websocket.WebSocketApp(self.WS_URL, on_open=on_open,
                                    on_message=on_message, on_error=on_error)
        ws.run_forever(reconnect=5)
