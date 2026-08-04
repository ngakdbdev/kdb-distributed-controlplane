"""
finnhub.py - LIVE adapter. wss://ws.finnhub.io?token=KEY
Free tier gives real-time US trades (and some FX/crypto). Subscribe per symbol.
"""
from __future__ import annotations

import json
import time

from .base import MarketDataProvider, ProviderError
from . import normalize


class FinnhubProvider(MarketDataProvider):
    name = "finnhub"
    display_name = "Finnhub"
    live = True
    coverage = "US equities (real-time on free tier), FX, crypto"
    requires = "a free Finnhub API key (FINNHUB_API_KEY)"

    WS_URL = "wss://ws.finnhub.io?token={key}"

    def _handle_raw(self, raw: str) -> int:
        """Parse one websocket frame and publish. Returns rows published.
        Split out from the socket loop so it's unit-testable with a fake
        publisher and no network."""
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return 0
        return self._publish(normalize.finnhub_trades(msg))

    def run(self) -> None:
        if not self.api_key:
            raise ProviderError("finnhub needs an API key (FINNHUB_API_KEY)")
        import websocket  # lazy: websocket-client, only needed to actually run

        def on_open(ws):
            for i, sym in enumerate(self.symbols):
                ws.send(json.dumps({"type": "subscribe", "symbol": sym}))
                if i % 50 == 49:      # gentle throttle for large lists
                    time.sleep(0.25)
            self.log.info("subscribed to %d symbols", len(self.symbols))

        def on_message(ws, raw):
            self._handle_raw(raw)

        def on_error(ws, err):
            self.log.warning("finnhub ws error: %s", err)

        ws = websocket.WebSocketApp(self.WS_URL.format(key=self.api_key),
                                    on_open=on_open, on_message=on_message,
                                    on_error=on_error)
        ws.run_forever(reconnect=5)
