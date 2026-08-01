"""
yahoo.py - LIVE (polling) adapter for Yahoo Finance.

HONEST CAVEAT: Yahoo has no official public market-data API. This hits the
unofficial `query1.finance.yahoo.com` quote endpoint that yfinance and similar
libraries use. It works and is great for a free demo, but: quotes are delayed,
it's rate-limited and can change/break without notice, the endpoint may now
require a crumb/cookie (yfinance is the robust route if raw calls start 401ing),
and Yahoo's ToS restricts commercial/redistribution use. Don't build production
on it. It's a polled quote (not a trade feed), so it publishes delayed prices.
"""
from __future__ import annotations

import urllib.parse

from .polling import PollingProvider
from . import normalize


class YahooFinanceProvider(PollingProvider):
    name = "yahoo"
    display_name = "Yahoo Finance"
    live = True
    coverage = "global delayed quotes (polled) - equities, ETFs, indices, FX"
    requires = "no API key, but UNOFFICIAL endpoint: delayed, rate-limited, ToS-restricted, not for production"

    BASE = "https://query1.finance.yahoo.com/v7/finance/quote?symbols={syms}"

    def _url(self) -> str:
        return self.BASE.format(syms=urllib.parse.quote(",".join(self.symbols)))

    def poll(self) -> list:
        return normalize.yahoo_quotes(self._get_json(self._url()))
