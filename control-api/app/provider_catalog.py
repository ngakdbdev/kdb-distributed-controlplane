"""
provider_catalog.py - the control plane's view of the available market-data
provider adapters, for the Connectors UI to display.

The canonical adapters live in data-plane/feeds/providers/. That tree isn't on
the control-api import path (and pulls feed deps), so this is a small static
mirror of their catalog metadata - display only. Keep the two in sync when you
add or change a provider; there's no code path that depends on the values
matching beyond what the UI shows.
"""

PROVIDER_CATALOG = [
    {"name": "finnhub", "display_name": "Finnhub", "tier": "live",
     "coverage": "US equities (real-time on free tier), FX, crypto",
     "requires": "a free Finnhub API key"},
    {"name": "twelvedata", "display_name": "Twelve Data", "tier": "live",
     "coverage": "global equities incl. NSE/BSE, FX, crypto (plan-dependent)",
     "requires": "a Twelve Data API key (global exchanges need a paid plan)"},
    {"name": "polygon", "display_name": "Polygon.io", "tier": "live",
     "coverage": "US equities/options/FX/crypto (real-time on paid tiers)",
     "requires": "a Polygon.io API key"},
    {"name": "yahoo", "display_name": "Yahoo Finance", "tier": "live",
     "coverage": "global delayed quotes (polled) - equities, ETFs, indices, FX",
     "requires": "no key, but UNOFFICIAL endpoint: delayed, rate-limited, ToS-restricted, not for production"},
    {"name": "alphavantage", "display_name": "Alpha Vantage", "tier": "live",
     "coverage": "global delayed quotes (polled) - equities, FX, crypto",
     "requires": "a free Alpha Vantage API key; free tier is ~25 calls/day"},
    {"name": "nyse", "display_name": "NYSE (Pillar)", "tier": "licensed",
     "coverage": "US equities - NYSE primary feeds",
     "requires": "NYSE market-data agreement + entitlements, connectivity, and a Pillar feed handler"},
    {"name": "lseg", "display_name": "LSEG (Refinitiv Real-Time / RDP)", "tier": "licensed",
     "coverage": "global - LSE and consolidated vendor data via Refinitiv",
     "requires": "LSEG/Refinitiv credentials + entitlements and the LSEG Data Library or Real-Time SDK"},
    {"name": "nse", "display_name": "NSE (India)", "tier": "licensed",
     "coverage": "Indian equities - NSE",
     "requires": "NSE market-data licensing (exchange membership or a licensed vendor) and connectivity"},
    {"name": "bse", "display_name": "BSE (India)", "tier": "licensed",
     "coverage": "Indian equities - BSE",
     "requires": "BSE market-data licensing (exchange/vendor) and connectivity"},
]
