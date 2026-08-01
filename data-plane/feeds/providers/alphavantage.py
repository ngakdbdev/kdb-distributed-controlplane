"""
alphavantage.py - LIVE (polling) adapter for Alpha Vantage.

Official REST API with a free key. GLOBAL_QUOTE returns one symbol per call and
the free tier is heavily rate-limited (a couple dozen calls/day), so this polls
symbols round-robin at a gentle interval - fine for a demo, not a firehose. The
paid tiers lift the limits. Quotes are delayed on the free tier.
"""
from __future__ import annotations

from .polling import PollingProvider
from .base import ProviderError
from . import normalize


class AlphaVantageProvider(PollingProvider):
    name = "alphavantage"
    display_name = "Alpha Vantage"
    live = True
    coverage = "global delayed quotes (polled) - equities, FX, crypto"
    requires = "a free Alpha Vantage API key (ALPHAVANTAGE_API_KEY); free tier is ~25 calls/day"

    poll_interval = 15.0    # gentle: free tier is ~5 calls/min
    BASE = "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={sym}&apikey={key}"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cursor = 0    # round-robin through symbols, one per poll

    def poll(self) -> list:
        if not self.api_key:
            raise ProviderError("alphavantage needs an API key (ALPHAVANTAGE_API_KEY)")
        if not self.symbols:
            return []
        sym = self.symbols[self._cursor % len(self.symbols)]
        self._cursor += 1
        url = self.BASE.format(sym=sym, key=self.api_key)
        return normalize.alphavantage_quote(self._get_json(url))
