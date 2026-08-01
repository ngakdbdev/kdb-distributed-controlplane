"""
polygon.py - LIVE adapter. wss://socket.polygon.io/stocks
Auth frame first, then subscribe to trade channels (T.<sym>). Generous
real-time US coverage on paid tiers; the free tier is delayed/aggregates, so
check what your key entitles before demoing "real-time".
"""
from __future__ import annotations

import json

from .base import MarketDataProvider, ProviderError
from . import normalize


class PolygonProvider(MarketDataProvider):
    name = "polygon"
    display_name = "Polygon.io"
    live = True
    coverage = "US equities/options/FX/crypto (real-time on paid tiers)"
    requires = "a Polygon.io API key (POLYGON_API_KEY)"

    WS_URL = "wss://socket.polygon.io/stocks"

    def _handle_raw(self, raw: str) -> int:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return 0
        return self._publish(normalize.polygon_messages(msg))

    def run(self) -> None:
        if not self.api_key:
            raise ProviderError("polygon needs an API key (POLYGON_API_KEY)")
        import websocket  # lazy

        channels = ",".join(f"T.{s}" for s in self.symbols)

        def on_open(ws):
            ws.send(json.dumps({"action": "auth", "params": self.api_key}))
            ws.send(json.dumps({"action": "subscribe", "params": channels}))
            self.log.info("authed and subscribed to %d trade channels", len(self.symbols))

        def on_message(ws, raw):
            self._handle_raw(raw)

        def on_error(ws, err):
            self.log.warning("polygon ws error: %s", err)

        ws = websocket.WebSocketApp(self.WS_URL, on_open=on_open,
                                    on_message=on_message, on_error=on_error)
        ws.run_forever(reconnect=5)
