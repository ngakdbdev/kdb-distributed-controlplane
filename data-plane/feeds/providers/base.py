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


def iso_to_dt(s) -> Optional[datetime]:
    """RFC 3339 / ISO 8601 with a trailing 'Z' (Coinbase, Kraken v2) - the
    zone offset stdlib fromisoformat wants instead of 'Z' pre-3.11, so swap
    it explicitly rather than assume a Python version."""
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def http_get_json(url: str, timeout: float = 20.0, headers: Optional[dict] = None) -> dict:
    """Plain GET -> parsed JSON, stdlib only. Used by each live provider's
    fetch_all_symbols() to pull the venue's REAL current instrument list
    (its own REST endpoint), instead of a hardcoded guess that drifts out
    of date the moment a symbol is listed/delisted/halted. `headers` merges
    into (and can override) the default User-Agent - the crypto venues'
    instrument lists are public and need nothing extra, but Alpaca's
    /v2/assets is an authenticated endpoint and passes its API key/secret
    here."""
    import json
    import urllib.request
    req_headers = {"User-Agent": "kdb-control-plane"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def chunked(items: list, size: int):
    """Split a list into size-N pieces, preserving order. Used to respect a
    venue's per-connection/per-message subscription limit (e.g. Binance caps
    a single websocket connection at 1024 streams) when the requested symbol
    list is larger than that."""
    for i in range(0, len(items), size):
        yield items[i:i + size]


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

    def _publish(self, ticks: list, publisher=None) -> int:
        """Route + publish a batch of Ticks. Returns how many rows went out.
        `publisher` overrides self.publisher for providers that run several
        websocket connections concurrently in their own threads (Binance,
        past its per-connection stream cap) - feed_common.TickerplantConnection
        isn't thread-safe (its `.q` is a single mutable connection with no
        locking), so those providers give each connection thread its OWN
        publisher instead of racing on one shared one."""
        pub = publisher if publisher is not None else self.publisher
        rows = [t.row(self._shard_of) for t in ticks if t]
        if rows:
            pub.publish_rows("trade", rows)
        if self._on_tick:
            for t in ticks:
                if t:
                    self._on_tick(t)
        return len(rows)

    @abstractmethod
    def run(self) -> None:
        """Connect, subscribe, and publish until interrupted (blocking)."""
        ...
