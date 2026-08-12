"""
binance.py - LIVE adapter. wss://stream.binance.com:9443
Binance's public combined-stream trade feed - real trades, no API key or
account needed. Symbols are Binance's own concatenated pairs (lowercase on
the wire, e.g. btcusdt for BTC/USDT); the highest-volume spot exchange
globally, so this is the single biggest lever for real message throughput
of any free feed in this repo.

fetch_all_symbols() pulls Binance's own live instrument list (GET
/api/v3/exchangeInfo) - the real, current tradable universe (~1,400 pairs at
time of writing), not a hardcoded guess that drifts as pairs list/delist.
Binance's combined-stream URL caps a single connection at 1024 streams, so
subscribing to the full universe needs MULTIPLE connections - run() shards
the symbol list into STREAM_CHUNK-sized groups and runs one WebSocketApp per
group, each in its own thread, all publishing through the same
ShardedPublisher.
"""
from __future__ import annotations

import json
import threading

from .base import MarketDataProvider, chunked, http_get_json
from . import normalize

EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"


class BinanceProvider(MarketDataProvider):
    name = "binance"
    display_name = "Binance"
    live = True
    coverage = "crypto spot - real-time trades, no key required, highest volume of any free feed here"
    requires = "nothing - public feed (symbols in Binance's own pair format, e.g. BTCUSDT)"

    WS_BASE = "wss://stream.binance.com:9443/stream?streams="
    STREAM_CHUNK = 1000  # Binance's documented cap is 1024 streams/connection - stay under it

    @staticmethod
    def fetch_all_symbols(fetch=http_get_json) -> list:
        """Every currently-TRADING spot pair, straight from Binance's own
        instrument list."""
        data = fetch(EXCHANGE_INFO_URL)
        return [s["symbol"] for s in data.get("symbols", []) if s.get("status") == "TRADING"]

    def _stream_url(self, symbols=None) -> str:
        symbols = self.symbols if symbols is None else symbols
        return self.WS_BASE + "/".join(f"{s.lower()}@trade" for s in symbols)

    def _handle_raw(self, raw: str, publisher=None) -> int:
        """Parse one websocket frame and publish. Split out from the socket
        loop so it's unit-testable with a fake publisher and no network."""
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return 0
        return self._publish(normalize.binance_trade(msg), publisher=publisher)

    def _run_one_connection(self, symbols: list) -> None:
        import websocket  # lazy: websocket-client, only needed to actually run

        # Each connection thread gets its OWN dedicated publisher (its own
        # TickerplantConnections) rather than sharing self.publisher across
        # threads - confirmed live that racing multiple threads on one
        # shared publisher corrupts its connection state ("QConnection
        # object has no attribute '_writer'"), since
        # feed_common.TickerplantConnection has no locking. Falls back to
        # self.publisher when there's only ever one connection (the common
        # case, symbols under STREAM_CHUNK), so single-connection callers
        # and tests that inject their own publisher/fetch still see it used.
        publisher = self.publisher
        if len(list(chunked(self.symbols, self.STREAM_CHUNK))) > 1:
            from feed_common import ShardedPublisher  # lazy: keeps this importable without the feeds tree
            publisher = ShardedPublisher(self.name, self.shard_count)

        def on_open(ws):
            self.log.info("subscribed to %d symbols on this connection", len(symbols))

        def on_message(ws, raw):
            self._handle_raw(raw, publisher=publisher)

        def on_error(ws, err):
            self.log.warning("binance ws error: %s", err)

        ws = websocket.WebSocketApp(self._stream_url(symbols), on_open=on_open,
                                    on_message=on_message, on_error=on_error)
        ws.run_forever(reconnect=5)

    def run(self) -> None:
        chunks = list(chunked(self.symbols, self.STREAM_CHUNK))
        if len(chunks) <= 1:
            self._run_one_connection(self.symbols)
            return
        self.log.info("splitting %d symbols across %d connections (%d/connection cap)",
                      len(self.symbols), len(chunks), self.STREAM_CHUNK)
        threads = [threading.Thread(target=self._run_one_connection, args=(c,), daemon=True)
                  for c in chunks]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
