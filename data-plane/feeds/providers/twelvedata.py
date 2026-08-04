"""
twelvedata.py - LIVE adapter. wss://ws.twelvedata.com/v1/quotes/price?apikey=KEY
Broadest global coverage of the live tier - US plus many international
exchanges including India (NSE/BSE) on the appropriate plan - so it's the one
to reach for when the demo needs non-US symbols.
"""
from __future__ import annotations

import json
import time

from .base import MarketDataProvider, ProviderError
from . import normalize


class TwelveDataProvider(MarketDataProvider):
    name = "twelvedata"
    display_name = "Twelve Data"
    live = True
    coverage = "global equities incl. NSE/BSE, FX, crypto (plan-dependent)"
    requires = "a Twelve Data API key (TWELVEDATA_API_KEY); global exchanges need a paid plan"

    WS_URL = "wss://ws.twelvedata.com/v1/quotes/price?apikey={key}"

    def _handle_raw(self, raw: str) -> int:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return 0
        return self._publish(normalize.twelvedata_price(msg))

    def run(self) -> None:
        if not self.api_key:
            raise ProviderError("twelvedata needs an API key (TWELVEDATA_API_KEY)")
        import websocket  # lazy

        def on_open(ws):
            batch = 120                        # chunk large lists across messages
            for i in range(0, len(self.symbols), batch):
                chunk = self.symbols[i:i + batch]
                ws.send(json.dumps({"action": "subscribe",
                                    "params": {"symbols": ",".join(chunk)}}))
                time.sleep(0.2)
            self.log.info("subscribed to %d symbols", len(self.symbols))

        def on_message(ws, raw):
            self._handle_raw(raw)

        def on_error(ws, err):
            self.log.warning("twelvedata ws error: %s", err)

        ws = websocket.WebSocketApp(self.WS_URL.format(key=self.api_key),
                                    on_open=on_open, on_message=on_message,
                                    on_error=on_error)
        ws.run_forever(reconnect=5)
