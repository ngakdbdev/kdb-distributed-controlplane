"""
yahoo.py - LIVE (polling) adapter for Yahoo Finance.

HONEST CAVEAT: Yahoo has no official public market-data API. This hits the
unofficial `query1.finance.yahoo.com` quote endpoint that yfinance and similar
libraries use. It works and is great for a free demo, but: quotes are delayed,
it's rate-limited and can change/break without notice, and Yahoo's ToS
restricts commercial/redistribution use. Don't build production on it. It's a
polled quote (not a trade feed), so it publishes delayed prices.

The endpoint now requires a session cookie + CSRF "crumb" token (a change
from when it was wide open) - a bare request 401s. _fetch_crumb below does
the same two-step handshake yfinance/similar libraries use: hit the consent
cookie endpoint, then the crumb endpoint, sharing one cookie jar across both
and every subsequent quote poll. Note this is still an unofficial, reverse-
engineered flow: Yahoo can (and does, especially from datacenter/cloud IPs)
block or CAPTCHA it without notice regardless of a valid crumb - if polls
keep 401ing/403ing even after this, that's Yahoo blocking the IP, not a
crumb bug, and yfinance (or a paid provider) is the robust alternative.
"""
from __future__ import annotations

import http.cookiejar
import urllib.error
import urllib.parse
import urllib.request

from .polling import PollingProvider
from . import normalize


class YahooFinanceProvider(PollingProvider):
    name = "yahoo"
    display_name = "Yahoo Finance"
    live = True
    coverage = "global delayed quotes (polled) - equities, ETFs, indices, FX"
    requires = "no API key, but UNOFFICIAL endpoint: delayed, rate-limited, ToS-restricted, not for production"

    BASE = "https://query1.finance.yahoo.com/v7/finance/quote?symbols={syms}&crumb={crumb}"
    CONSENT_URL = "https://fc.yahoo.com"
    CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cookiejar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookiejar))
        self._crumb = None

    def _fetch_crumb(self) -> str:
        for url in (self.CONSENT_URL, self.CRUMB_URL):
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            resp = self._opener.open(req, timeout=10)
            body = resp.read().decode()
        return body.strip()  # last response read above is the crumb endpoint's

    def _url(self) -> str:
        # self._fetch is the test seam (PollingProvider) - when it's set
        # we're running against canned data, not the real network, so skip
        # the real crumb handshake entirely rather than let it 401/network-
        # error out from inside what's meant to be an offline unit test.
        if self._crumb is None and self._fetch is None:
            self._crumb = self._fetch_crumb()
        crumb = self._crumb or ""
        return self.BASE.format(syms=urllib.parse.quote(",".join(self.symbols)),
                                crumb=urllib.parse.quote(crumb))

    def _get_json(self, url: str) -> dict:
        # Overrides PollingProvider._get_json: this provider's requests need
        # the SAME cookie jar the crumb was fetched with attached, or the
        # quote request 401s even with a valid crumb in the query string.
        if self._fetch is not None:
            return self._fetch(url)
        import json
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with self._opener.open(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def poll(self) -> list:
        try:
            return normalize.yahoo_quotes(self._get_json(self._url()))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                # crumb/cookie went stale (or was wrong) - drop it so the
                # NEXT poll re-runs the handshake instead of repeating the
                # same failure every cycle forever.
                self._crumb = None
            raise
