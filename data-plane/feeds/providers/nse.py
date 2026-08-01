"""
nse.py - LICENSED scaffold. NSE (India) proprietary market data.

Real integration seam: NSE real-time data is delivered as a licensed exchange
feed (multicast / tick-by-tick) or via a licensed data vendor - it requires
exchange membership or a data-licensing agreement plus connectivity. There is
no free real-time public API. Build a feed handler to NSE's current spec (or
use your licensed vendor's SDK) and hand trades to `_publish`.
"""
from __future__ import annotations

from .base import MarketDataProvider, ProviderNotConfigured


class NSEProvider(MarketDataProvider):
    name = "nse"
    display_name = "NSE (India)"
    live = False
    coverage = "Indian equities - NSE"
    requires = "NSE market-data licensing (exchange membership or a licensed vendor) and feed connectivity"

    def run(self) -> None:
        raise ProviderNotConfigured(
            "NSE is a licensed exchange feed. To go live: obtain NSE market-data licensing "
            "(exchange membership or a licensed vendor) and connectivity, then wire a feed "
            "handler to _publish(). See this file's docstring.")
