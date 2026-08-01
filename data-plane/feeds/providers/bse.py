"""
bse.py - LICENSED scaffold. BSE (India) proprietary market data.

Real integration seam: BSE real-time data is delivered as a licensed exchange
feed (multicast) or via a licensed data vendor - it requires exchange
membership or a data-licensing agreement plus connectivity. There is no free
real-time public API. Build a feed handler to BSE's current spec (or use your
licensed vendor's SDK) and hand trades to `_publish`.
"""
from __future__ import annotations

from .base import MarketDataProvider, ProviderNotConfigured


class BSEProvider(MarketDataProvider):
    name = "bse"
    display_name = "BSE (India)"
    live = False
    coverage = "Indian equities - BSE"
    requires = "BSE market-data licensing (exchange/vendor) and feed connectivity"

    def run(self) -> None:
        raise ProviderNotConfigured(
            "BSE is a licensed exchange feed. To go live: obtain BSE market-data licensing "
            "(exchange or a licensed vendor) and connectivity, then wire a feed handler to "
            "_publish(). See this file's docstring.")
