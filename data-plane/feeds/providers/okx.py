"""
okx.py - LIVE adapter. wss://ws.okx.com:8443/ws/v5/public
OKX's public v5 "trades" channel - real trades, no API key or account
needed. Symbols are OKX's own hyphenated pairs, e.g. BTC-USDT.

fetch_all_symbols() pulls OKX's own live instrument list (GET
/api/v5/public/instruments?instType=SPOT) - the real, current tradable
universe, not a hardcoded guess. Subscriptions are batched (SUBSCRIBE_CHUNK
channels per "subscribe" frame, several frames on the SAME connection) -
OKX documents a 480-arg-per-request ceiling, well below what a full
universe needs in one frame.
"""
from __future__ import annotations

import json

from .base import MarketDataProvider, chunked, http_get_json
from . import normalize

INSTRUMENTS_URL = "https://www.okx.com/api/v5/public/instruments?instType=SPOT"


class OKXProvider(MarketDataProvider):
    name = "okx"
    display_name = "OKX"
    live = True
    coverage = "crypto spot - real-time trades, no key required"
    requires = "nothing - public feed (symbols in OKX's own pair format, e.g. BTC-USDT)"

    WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
    SUBSCRIBE_CHUNK = 200  # OKX documents a 480 args/request ceiling; stay well under it

    @staticmethod
    def fetch_all_symbols(fetch=http_get_json) -> list:
        """Every currently-live spot instrument, straight from OKX's own
        instrument list."""
        data = fetch(INSTRUMENTS_URL)
        return [r["instId"] for r in data.get("data") or [] if r.get("state") == "live"]

    def _handle_raw(self, raw: str) -> int:
        """Parse one websocket frame and publish. Split out from the socket
        loop so it's unit-testable with a fake publisher and no network."""
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return 0
        return self._publish(normalize.okx_trade(msg))

    def run(self) -> None:
        import websocket  # lazy: websocket-client, only needed to actually run

        def on_open(ws):
            args = [{"channel": "trades", "instId": s} for s in self.symbols]
            for batch in chunked(args, self.SUBSCRIBE_CHUNK):
                ws.send(json.dumps({"op": "subscribe", "args": batch}))
            self.log.info("subscribed to %d symbols (%d batches)", len(self.symbols),
                         -(-len(args) // self.SUBSCRIBE_CHUNK))

        def on_message(ws, raw):
            self._handle_raw(raw)

        def on_error(ws, err):
            self.log.warning("okx ws error: %s", err)

        ws = websocket.WebSocketApp(self.WS_URL, on_open=on_open,
                                    on_message=on_message, on_error=on_error)
        ws.run_forever(reconnect=5)
