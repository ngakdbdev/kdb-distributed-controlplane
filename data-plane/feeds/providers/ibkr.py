"""
ibkr.py - LIVE (polling) adapter for Interactive Brokers' Client Portal Web
API (CPAPI) market-data snapshots. Level 1 quotes (last price, volume).

HONEST CAVEAT, read before deploying this: unlike every other provider in
this directory, IBKR has no stateless cloud endpoint to just call with an
API key. CPAPI is served by a **locally-running Client Portal Gateway** (a
small Java process IBKR ships) that must already be started AND
authenticated (username/password + 2FA) before this adapter can reach it -
see IBKR_GATEWAY_BASE_URL below. Keeping that gateway alive and logged in
(daily/weekly re-auth) is a real, ongoing operational task this adapter does
NOT solve; the community-standard tool for automating it is IBeam
(https://github.com/Voyz/ibeam) - run it as its own service and point
IBKR_GATEWAY_BASE_URL at whatever host:port it exposes. This module is coded
carefully to CPAPI's documented request/response shapes but has not been
exercised against a live, authenticated gateway in this codebase's own CI -
treat it the way this repo treats its "licensed" feeds (NYSE/LSEG/NSE/BSE):
correct to spec, verify against your own real gateway before trusting it.

Two real CPAPI quirks this code works around:
  * Symbols aren't queried directly - CPAPI addresses instruments by numeric
    "conid" (contract ID). _resolve_conids() looks each symbol up once via
    /iserver/secdef/search and caches the result for the life of the process.
  * A snapshot request for a conid IBKR hasn't "warmed up" market data for
    yet often comes back with fields missing on the FIRST call (documented
    IBKR behavior, not a bug here) - this adapter doesn't special-case that;
    it just means the very first poll or two after a symbol is first
    requested may not publish anything for it, which is harmless under a
    polling loop (the next cycle picks it up).

The local gateway serves HTTPS with a self-signed certificate by default
(every IBKR CPAPI integration guide instructs disabling verification for
it) - IBKR_GATEWAY_VERIFY_SSL defaults to "false" for exactly that reason;
set it to "true" only if you've placed a real certificate in front of your
gateway. This is a materially different trust boundary than a random
internet host: you're pointing at infrastructure you (or IBeam) run.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request

from .polling import PollingProvider
from . import normalize

# Level 1 field codes CPAPI's /iserver/marketdata/snapshot understands -
# see IBKR's Web API docs ("Market Data Fields"). 31=last price,
# 87=volume (cumulative session volume, not a per-trade size - same
# simplification normalize.twelvedata_price already makes for that vendor's
# day_volume field, kept consistent here rather than inventing a different
# convention for this one provider).
_FIELDS = "31,87"


class IBKRProvider(PollingProvider):
    name = "ibkr"
    display_name = "Interactive Brokers"
    live = True
    coverage = "Level 1 quotes - whatever your IBKR account/gateway is entitled to (free Cboe One/IEX for US names; full consolidated NBBO and non-US exchanges typically need a paid IBKR market-data subscription)"
    requires = ("a running, already-authenticated IBKR Client Portal Gateway (IBKR_GATEWAY_BASE_URL) - "
               "see this module's own docstring; this is NOT a simple API-key integration, the gateway "
               "process and its login/2FA lifecycle are yours to operate (IBeam is the community-standard "
               "tool for that)")

    poll_interval = 3.0

    def __init__(self, *args, gateway_base_url: str = "", verify_ssl: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.gateway_base_url = (gateway_base_url or "https://localhost:5000/v1/api").rstrip("/")
        self.verify_ssl = verify_ssl
        self._conids = {}    # symbol -> conid, resolved lazily and cached
        self._ssl_ctx = None if verify_ssl else _insecure_context()

    def _get_json(self, url: str) -> dict:
        if self._fetch is not None:
            return self._fetch(url)
        req = urllib.request.Request(f"{self.gateway_base_url}{url}",
                                     headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=10, context=self._ssl_ctx) as resp:
            return json.loads(resp.read().decode())

    def _resolve_conid(self, symbol: str):
        if symbol in self._conids:
            return self._conids[symbol]
        try:
            matches = self._get_json(f"/iserver/secdef/search?symbol={symbol}")
        except (urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
            self.log.warning("ibkr: could not resolve conid for %s: %s", symbol, exc)
            return None
        conid = None
        for m in matches or []:
            if isinstance(m, dict) and m.get("conid"):
                conid = m["conid"]
                break
        self._conids[symbol] = conid  # cache the miss too - don't re-query every poll for a bad symbol
        if conid is None:
            self.log.warning("ibkr: no conid found for symbol %s", symbol)
        return conid

    def poll(self) -> list:
        conids = [c for c in (self._resolve_conid(s) for s in self.symbols) if c is not None]
        if not conids:
            return []
        conid_to_symbol = {v: k for k, v in self._conids.items() if v is not None}
        raw = self._get_json(f"/iserver/marketdata/snapshot?conids={','.join(str(c) for c in conids)}&fields={_FIELDS}")
        return normalize.ibkr_snapshot(raw, conid_to_symbol)

    def run(self) -> None:
        self.log.info("polling %d symbols against gateway %s every %.1fs",
                      len(self.symbols), self.gateway_base_url, self.poll_interval)
        super().run()


def _insecure_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
