"""
lseg.py - LICENSED scaffold. LSEG (Refinitiv) real-time market data.

Real integration seam: LSEG exposes real-time data through the Refinitiv
Real-Time platform (streaming via the Real-Time SDK / EMA, or Refinitiv
Real-Time Optimized with machine credentials) and through the LSEG Data
Library / Data Platform (RDP) streaming APIs. All of it is licensed and
entitled. When configured, open a session with the `lseg-data` library (or
EMA), subscribe to your instruments, and hand each update to `_publish`. Until
you have LSEG credentials + entitlements it can't connect - so it says so
rather than faking it.
"""
from __future__ import annotations

from .base import MarketDataProvider, ProviderNotConfigured


class LSEGProvider(MarketDataProvider):
    name = "lseg"
    display_name = "LSEG (Refinitiv Real-Time / RDP)"
    live = False
    coverage = "global - LSE and consolidated vendor data via Refinitiv"
    requires = "LSEG/Refinitiv credentials + entitlements and the LSEG Data Library or Real-Time SDK"

    def run(self) -> None:
        # The real path looks like:
        #     import lseg.data as ld
        #     ld.open_session(app_key=..., ...)     # machine creds / RDP
        #     stream = ld.content.pricing.Definition(self.symbols).get_stream()
        #     stream.on_update(lambda *_: self._publish(...))  # normalize -> Ticks
        # gated behind licensing, so we don't pretend:
        raise ProviderNotConfigured(
            "LSEG/Refinitiv real-time is a licensed, entitled feed. To go live: provision "
            "LSEG credentials + entitlements, install the LSEG Data Library (or Real-Time "
            "SDK/EMA), open a session, and route price updates to _publish(). See this "
            "file's docstring for the seam.")
