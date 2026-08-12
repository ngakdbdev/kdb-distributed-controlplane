"""
binance_depth.py - LIVE adapter for Binance's L2 order-book DELTA feed
(<sym>@depth@100ms), not trade prints.

Every bid/ask price-level change is its own message - a resting order being
placed, cancelled, or modified moves the book without ever trading, so real
book activity runs at a far higher message rate than trade executions ever
will. This is the actual lever for materially higher REAL message
throughput once you've already subscribed to every tradable symbol's trade
feed (data-plane/feeds/providers/binance.py) and hit the ceiling of how
often real trades actually print.

Scope this to a FEW liquid pairs, not the whole universe: a single symbol's
depth stream at 100ms intervals, each carrying several changed price
levels, is already substantially higher-volume than that same symbol's
trade prints - subscribing hundreds/thousands of symbols to depth the same
way binance.py subscribes trades would be genuinely excessive load for this
deployment's scale.

Rows land in the SAME `trade` table every other feed here uses (see
normalize.binance_depth's own docstring for why: reusing the existing
schema, not adding a new `book` table across every q process tonight) -
venue="binance-depth" and side="BID"/"ASK" make these unambiguous against
real trade prints.
"""
from __future__ import annotations

import json

from .base import MarketDataProvider
from . import normalize


class BinanceDepthProvider(MarketDataProvider):
    name = "binance-depth"
    display_name = "Binance (order book)"
    live = True
    coverage = "crypto L2 order-book deltas - real bid/ask changes, far higher message rate than trade prints"
    requires = ("nothing - public feed. Keep the symbol list SHORT (a handful of liquid pairs): "
               "depth updates are much higher-volume per symbol than trade prints")

    WS_BASE = "wss://stream.binance.com:9443/stream?streams="

    def _stream_url(self) -> str:
        return self.WS_BASE + "/".join(f"{s.lower()}@depth@100ms" for s in self.symbols)

    def _handle_raw(self, raw: str) -> int:
        """Parse one websocket frame and publish. Split out from the socket
        loop so it's unit-testable with a fake publisher and no network."""
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return 0
        return self._publish(normalize.binance_depth(msg))

    def run(self) -> None:
        import websocket  # lazy: websocket-client, only needed to actually run

        def on_open(ws):
            self.log.info("subscribed to order-book deltas for %d symbols", len(self.symbols))

        def on_message(ws, raw):
            self._handle_raw(raw)

        def on_error(ws, err):
            self.log.warning("binance depth ws error: %s", err)

        ws = websocket.WebSocketApp(self._stream_url(), on_open=on_open,
                                    on_message=on_message, on_error=on_error)
        ws.run_forever(reconnect=5)
