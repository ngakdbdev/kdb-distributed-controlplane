"""
coinbase.py - LIVE adapter. wss://ws-feed.exchange.coinbase.com
Coinbase Exchange's public market-data feed - real trades, no API key or
account needed (this is the public "matches" channel, not the authenticated
trading API). Symbols are Coinbase's own hyphenated pairs, e.g. BTC-USD.

fetch_all_symbols() pulls Coinbase's own live product list (GET /products) -
the real, current tradable universe, not a hardcoded guess. Subscriptions
are batched (SUBSCRIBE_CHUNK product_ids per "subscribe" frame, several
frames on the SAME connection) rather than one giant message.
"""
from __future__ import annotations

import json

from .base import MarketDataProvider, chunked, http_get_json
from . import normalize

PRODUCTS_URL = "https://api.exchange.coinbase.com/products"


class CoinbaseProvider(MarketDataProvider):
    name = "coinbase"
    display_name = "Coinbase"
    live = True
    coverage = "crypto spot - real-time trades, no key required"
    requires = "nothing - public feed (symbols in Coinbase's own pair format, e.g. BTC-USD)"

    WS_URL = "wss://ws-feed.exchange.coinbase.com"
    SUBSCRIBE_CHUNK = 200

    @staticmethod
    def fetch_all_symbols(fetch=http_get_json) -> list:
        """Every currently-online product, straight from Coinbase's own
        product list."""
        data = fetch(PRODUCTS_URL)
        return [r["id"] for r in data if r.get("status") == "online"]

    def _handle_raw(self, raw: str) -> int:
        """Parse one websocket frame and publish. Split out from the socket
        loop so it's unit-testable with a fake publisher and no network."""
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return 0
        return self._publish(normalize.coinbase_match(msg))

    def run(self) -> None:
        import websocket  # lazy: websocket-client, only needed to actually run

        def on_open(ws):
            for batch in chunked(self.symbols, self.SUBSCRIBE_CHUNK):
                ws.send(json.dumps({
                    "type": "subscribe",
                    "product_ids": batch,
                    "channels": ["matches"],
                }))
            self.log.info("subscribed to %d symbols", len(self.symbols))

        def on_message(ws, raw):
            self._handle_raw(raw)

        def on_error(ws, err):
            self.log.warning("coinbase ws error: %s", err)

        ws = websocket.WebSocketApp(self.WS_URL, on_open=on_open,
                                    on_message=on_message, on_error=on_error)
        ws.run_forever(reconnect=5)
