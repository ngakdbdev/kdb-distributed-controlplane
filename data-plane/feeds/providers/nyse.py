"""
nyse.py - LICENSED scaffold. NYSE proprietary market data (Pillar).

Real integration seam: NYSE's direct feeds (NYSE Integrated / Pillar) are
binary messages over UDP multicast, delivered via co-location or the Secure
Financial Transaction Infrastructure / an extranet vendor. Consuming them
needs (1) a signed NYSE market-data agreement + entitlements, (2) network
connectivity to the feed, and (3) a binary feed handler built to the current
NYSE Pillar spec that decodes messages and hands trades to `_publish`. There
is no public SDK/key - this is where your licensed feed handler plugs in.
"""
from __future__ import annotations

from .base import MarketDataProvider, ProviderNotConfigured


class NYSEProvider(MarketDataProvider):
    name = "nyse"
    display_name = "NYSE (Pillar)"
    live = False
    coverage = "US equities - NYSE primary feeds"
    requires = "NYSE market-data agreement + entitlements, feed connectivity, and a Pillar binary feed handler"

    def run(self) -> None:
        raise ProviderNotConfigured(
            "NYSE Pillar is a licensed binary multicast feed. To go live: sign the "
            "NYSE market-data agreement, provision feed connectivity (co-lo/extranet), "
            "and wire a Pillar feed handler to _publish(). See this file's docstring for "
            "the integration seam.")
