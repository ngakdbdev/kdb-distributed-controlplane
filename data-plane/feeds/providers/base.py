"""
base.py - the shared provider interface and the canonical Tick -> row plumbing.

`Tick` is vendor-agnostic; `normalize.py` turns each vendor's payload into
Ticks (pure, unit-tested), and `MarketDataProvider._publish` turns Ticks into
the exact `[ts, sym, price, size, side, venue, shard]` row the tickerplant
expects, routed by topology.shard_of. Adapters only have to parse their feed
and hand back Ticks - routing, batching, and publishing are handled here.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

log = logging.getLogger("providers")


@dataclass
class Tick:
    symbol: str
    price: float
    size: int = 0
    side: str = ""          # "B" / "S" / "" (many venues don't tag aggressor side)
    venue: str = ""
    ts: Optional[datetime] = None

    def row(self, shard_of: Callable[[str], str]) -> list:
        """The canonical trade row: matches bpipe_sim.gen_trade exactly."""
        ts = self.ts or datetime.now(timezone.utc)
        return [ts, self.symbol, float(self.price), int(self.size or 0),
                self.side or "", self.venue or "", shard_of(self.symbol)]


class ProviderError(RuntimeError):
    pass


class ProviderNotConfigured(ProviderError):
    """Raised by a LICENSED provider that has no credentials/entitlements yet.
    Carries a human message pointing at exactly what's needed."""


def ms_to_dt(ms) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def sec_to_dt(sec) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(float(sec), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


class MarketDataProvider(ABC):
    # ---- catalog metadata (subclasses override) ----
    name = "base"
    display_name = "Base"
    live = False            # True: usable with a public API key; False: licensed
    coverage = ""           # e.g. "US equities", "global incl. NSE/BSE"
    requires = ""           # what a live connection needs

    def __init__(self, symbols: list, publisher, shard_count: int,
                 api_key: Optional[str] = None,
                 on_tick: Optional[Callable[[Tick], None]] = None):
        self.symbols = symbols
        self.publisher = publisher
        self.shard_count = shard_count
        self.api_key = api_key
        self._on_tick = on_tick     # optional test/inspection seam
        self.log = logging.getLogger(f"providers.{self.name}")

    def _shard_of(self, symbol: str) -> str:
        import topology  # lazy: keeps the module importable without the feeds tree
        return topology.shard_of(symbol, self.shard_count)

    def _publish(self, ticks: list) -> int:
        """Route + publish a batch of Ticks. Returns how many rows went out."""
        rows = [t.row(self._shard_of) for t in ticks if t]
        if rows:
            self.publisher.publish_rows("trade", rows)
        if self._on_tick:
            for t in ticks:
                if t:
                    self._on_tick(t)
        return len(rows)

    @abstractmethod
    def run(self) -> None:
        """Connect, subscribe, and publish until interrupted (blocking)."""
        ...
