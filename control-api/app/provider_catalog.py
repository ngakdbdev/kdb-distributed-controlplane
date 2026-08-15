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
    {"name": "alpaca", "display_name": "Alpaca", "tier": "live",
     "coverage": "US equities/ETFs - real-time IEX trades free, consolidated SIP tape on a paid plan",
     "requires": ("a free Alpaca account's API key ID + secret key (same credentials Alpaca paper "
                 "trading uses) - see the Bot page for order routing through this account")},
    {"name": "ibkr", "display_name": "Interactive Brokers", "tier": "live",
     "coverage": "Level 1 quotes - whatever your IBKR account/gateway is entitled to (free for basic US "
                "coverage; full NBBO and non-US exchanges typically need a paid market-data subscription)",
     "requires": ("a running, already-authenticated IBKR Client Portal Gateway - NOT a simple API key. "
                 "The gateway process and its login/2FA session lifecycle are yours to operate; IBeam "
                 "(github.com/Voyz/ibeam) is the community-standard tool for automating that. See the "
                 "Bot page for order routing through the same gateway")},
    {"name": "coinbase", "display_name": "Coinbase", "tier": "live",
     "coverage": "crypto spot - real-time trades, no key required",
     "requires": "nothing - public feed (symbols in Coinbase's own pair format, e.g. BTC-USD)"},
    {"name": "kraken", "display_name": "Kraken", "tier": "live",
     "coverage": "crypto spot - real-time trades, no key required",
     "requires": "nothing - public feed (symbols in Kraken's own pair format, e.g. BTC/USD)"},
    {"name": "binance", "display_name": "Binance", "tier": "live",
     "coverage": "crypto spot - real-time trades, no key required, highest volume of any free feed here",
     "requires": "nothing - public feed (symbols in Binance's own pair format, e.g. BTCUSDT)"},
    {"name": "binance-depth", "display_name": "Binance (order book)", "tier": "live",
     "coverage": "crypto L2 order-book deltas - real bid/ask changes, far higher message rate than trade prints",
     "requires": ("nothing - public feed. Keep the symbol list SHORT (a handful of liquid pairs): "
                 "depth updates are much higher-volume per symbol than trade prints")},
    {"name": "bybit", "display_name": "Bybit", "tier": "live",
     "coverage": "crypto spot - real-time trades, no key required",
     "requires": "nothing - public feed (symbols in Bybit's own pair format, e.g. BTCUSDT)"},
    {"name": "okx", "display_name": "OKX", "tier": "live",
     "coverage": "crypto spot - real-time trades, no key required",
     "requires": "nothing - public feed (symbols in OKX's own pair format, e.g. BTC-USDT)"},
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
