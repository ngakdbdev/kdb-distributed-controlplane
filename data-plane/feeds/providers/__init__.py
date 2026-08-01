"""
providers - pluggable market-data provider adapters for the tick pipeline.

Each provider implements the same small interface (base.MarketDataProvider):
connect, subscribe, and a run-loop that normalizes each vendor tick into the
canonical `trade` row and publishes it through the real ShardedPublisher -
routed across shards by topology.shard_of, exactly like the built-in sims. So
a real feed drops in behind the same on/off toggle the Connectors tab uses.

Two tiers:

* LIVE  - Twelve Data, Finnhub, Polygon: real data behind a public API key /
          free tier. You can actually run these today.
* LICENSED - NYSE (Pillar), LSEG (Refinitiv Real-Time / RDP), NSE, BSE: coded
          to the real SDK/protocol shape, but they only connect once you plug
          in your market-data agreement, entitlements, and connectivity. Until
          then they fail with a clear "needs licensing" message rather than
          pretending to be connected. This is the seam a prospect's own feed
          lands on.
"""
from .finnhub import FinnhubProvider
from .twelvedata import TwelveDataProvider
from .polygon import PolygonProvider
from .yahoo import YahooFinanceProvider
from .alphavantage import AlphaVantageProvider
from .nyse import NYSEProvider
from .lseg import LSEGProvider
from .nse import NSEProvider
from .bse import BSEProvider

_ALL = [
    FinnhubProvider, TwelveDataProvider, PolygonProvider,
    YahooFinanceProvider, AlphaVantageProvider,
    NYSEProvider, LSEGProvider, NSEProvider, BSEProvider,
]

PROVIDERS = {p.name: p for p in _ALL}


def get_provider(name: str):
    try:
        return PROVIDERS[name]
    except KeyError:
        raise KeyError(f"unknown provider '{name}'. known: {', '.join(sorted(PROVIDERS))}")


def catalog() -> list:
    """Metadata for every provider - what the UI 'market data providers' list
    shows (live vs licensed, coverage, what each one needs)."""
    return [
        {
            "name": p.name,
            "display_name": p.display_name,
            "live": p.live,
            "coverage": p.coverage,
            "requires": p.requires,
        }
        for p in _ALL
    ]
