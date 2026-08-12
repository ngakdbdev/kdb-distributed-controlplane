"""
kraken.py - LIVE adapter. wss://ws.kraken.com/v2
Kraken's public WebSocket API v2 - real trades, no API key needed (public
market data, not the authenticated trading API). Symbols are Kraken's own
slash-separated pairs, e.g. BTC/USD.

fetch_all_symbols() pulls Kraken's own live instrument list (GET
/0/public/AssetPairs) - the real, current tradable universe, not a
hardcoded guess. Note the REST endpoint's dict keys (e.g. "XXBTZUSD") are
Kraken's internal asset codes, NOT the websocket symbol - each pair's own
"wsname" field (e.g. "XBT/USD") is what the v2 websocket API actually wants,
so fetch_all_symbols reads that field specifically. Subscriptions are
batched (SUBSCRIBE_CHUNK symbols per "subscribe" frame, several frames on
the SAME connection) for the same reason as every other exchange here: stay
well under any per-message size the venue might otherwise reject.
"""
from __future__ import annotations

import json

from .base import MarketDataProvider, chunked, http_get_json
from . import normalize

ASSET_PAIRS_URL = "https://api.kraken.com/0/public/AssetPairs"


class KrakenProvider(MarketDataProvider):
    name = "kraken"
    display_name = "Kraken"
    live = True
    coverage = "crypto spot - real-time trades, no key required"
    requires = "nothing - public feed (symbols in Kraken's own pair format, e.g. BTC/USD)"

    WS_URL = "wss://ws.kraken.com/v2"
    SUBSCRIBE_CHUNK = 200

    @staticmethod
    def fetch_all_symbols(fetch=http_get_json) -> list:
        """Every tradable pair's websocket name ("wsname", e.g. BTC/USD) -
        straight from Kraken's own instrument list. The dict's own keys
        (e.g. XXBTZUSD) are internal asset codes, not usable on the
        websocket - wsname is the field that is."""
        data = fetch(ASSET_PAIRS_URL)
        rows = (data.get("result") or {}).values()
        return sorted({r["wsname"] for r in rows if r.get("wsname")})

    def _handle_raw(self, raw: str) -> int:
        """Parse one websocket frame and publish. Split out from the socket
        loop so it's unit-testable with a fake publisher and no network."""
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return 0
        return self._publish(normalize.kraken_trade(msg))

    def run(self) -> None:
        import websocket  # lazy: websocket-client, only needed to actually run

        def on_open(ws):
            for batch in chunked(self.symbols, self.SUBSCRIBE_CHUNK):
                ws.send(json.dumps({
                    "method": "subscribe",
                    "params": {"channel": "trade", "symbol": batch},
                }))
            self.log.info("subscribed to %d symbols", len(self.symbols))

        def on_message(ws, raw):
            self._handle_raw(raw)

        def on_error(ws, err):
            self.log.warning("kraken ws error: %s", err)

        ws = websocket.WebSocketApp(self.WS_URL, on_open=on_open,
                                    on_message=on_message, on_error=on_error)
        ws.run_forever(reconnect=5)
