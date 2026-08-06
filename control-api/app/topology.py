"""
topology.py - the single source of truth for shard topology.

The whole "highly scalable, multi-instance" story rests on one idea: shard
count is a single number, and *everything* - the q processes that run, the
services the watchdog heals, the ports the gateway dials, the tickerplants a
feed fans out to, the PVCs Helm provisions - is derived from it. Nothing
hardcodes a 2-way A-M/N-Z split anymore.

Design choices, and why:

* Shards have STABLE INDEX IDS (`s0`, `s1`, ...). The A-M/N-Z letter range a
  shard owns is a *property* (its `label`/`lo`/`hi`), not its identity - so
  changing the shard count re-partitions the alphabet without renaming the
  shard a given process belongs to in a confusing way.

* PORTS ARE UNIFORM PER TIER (every tickerplant listens on 5010, every rdb on
  5020, ...). Each process is its own container/pod with its own network
  identity (`rdb-s0`, `rdb-s1`), so they can share a port. This removes all
  per-shard port arithmetic - the thing that made the old hand-written list
  drift.

* The alphabet is partitioned into N contiguous, as-even-as-possible ranges.
  At N=2 this reproduces the exact A-M / N-Z split the MVP shipped with, so
  behaviour is unchanged for the default; at N=4 you get A-G / H-N / O-T /
  U-Z, and so on. Contiguous ranges (rather than hash-mod-N) preserve range
  locality so a symbol range-scan hits one shard, and they're auditable - the
  control plane can *show* an operator exactly which shard owns which symbols.

This module is intentionally dependency-free (standard library only) so the
identical file can be vendored into the control-api, watchdog, and feed
images without dragging FastAPI/SQLModel along. `scripts/check_topology_sync.py`
asserts the copies stay byte-identical.
"""
from __future__ import annotations

import string
from dataclasses import dataclass, asdict
from typing import Optional

ALPHABET = string.ascii_uppercase  # 26 letters
MAX_SHARDS = len(ALPHABET)         # contiguous-range routing tops out at 26

# uniform per-tier listen ports - see module docstring
TIER_PORTS = {
    "tickerplant": 5010,
    "rdb": 5020,
    "idb": 5030,
    "wdb": 5040,
    "gateway": 5050,
    "hdb": 5060,
}

# the five per-shard q processes, in dependency order. hdb depends on wdb
# because it loads the partitioned directory wdb's end-of-day seal writes into
# - it has nothing to serve until wdb has sealed at least one day.
SHARD_TIERS = ("tickerplant", "wdb", "rdb", "idb", "hdb")

# feed simulators - not sharded, not healed as failures (their on/off state is
# a deliberate user choice via the Connectors screen), listed here so callers
# that need "every managed container" have one place to get it
FEED_SERVICES = ("bpipe-sim", "crims-sim")


def _validate(n: int) -> None:
    if not isinstance(n, int) or n < 1:
        raise ValueError(f"shard count must be a positive integer, got {n!r}")
    if n > MAX_SHARDS:
        raise ValueError(
            f"contiguous-range routing supports at most {MAX_SHARDS} shards "
            f"(one per letter); got {n}. Beyond that you'd switch to hash "
            f"routing - out of scope for this build."
        )


@dataclass(frozen=True)
class Shard:
    index: int
    id: str          # stable identity, e.g. "s0"
    lo: str          # inclusive first-letter range start, e.g. "A"
    hi: str          # inclusive first-letter range end,   e.g. "M"

    @property
    def label(self) -> str:
        return self.lo if self.lo == self.hi else f"{self.lo}-{self.hi}"

    def service(self, tier: str) -> str:
        """Container/Service/Deployment name for one tier of this shard."""
        prefix = "tp" if tier == "tickerplant" else tier
        return f"{prefix}-{self.id}"

    @property
    def db_volume(self) -> str:
        return f"db-{self.id}"

    @property
    def hdb_volume(self) -> str:
        return f"hdb-{self.id}"

    @property
    def tp_log_volume(self) -> str:
        return f"tp-log-{self.id}"


def letter_ranges(n: int) -> list[tuple[str, str]]:
    """Partition A-Z into n contiguous, as-even-as-possible ranges.

    The first (26 mod n) ranges get one extra letter. n=2 -> [(A,M),(N,Z)].
    """
    _validate(n)
    base, extra = divmod(len(ALPHABET), n)
    ranges: list[tuple[str, str]] = []
    idx = 0
    for i in range(n):
        size = base + (1 if i < extra else 0)
        ranges.append((ALPHABET[idx], ALPHABET[idx + size - 1]))
        idx += size
    return ranges


def shards(n: int) -> list[Shard]:
    return [
        Shard(index=i, id=f"s{i}", lo=lo, hi=hi)
        for i, (lo, hi) in enumerate(letter_ranges(n))
    ]


def shard_of(sym: str, n: int) -> str:
    """Return the shard id that owns `sym`, by its first alphabetic letter.

    Symbols with no leading A-Z letter (digits, punctuation) fall to s0, which
    is documented and matches how the gateway's fallback behaves.
    """
    c = next((ch for ch in sym.upper() if ch in ALPHABET), None)
    if c is None:
        return "s0"
    for s in shards(n):
        if s.lo <= c <= s.hi:
            return s.id
    return "s0"  # unreachable given a full A-Z partition, kept for safety


def gateway_host(shard: Shard, tier: str) -> str:
    """host:port a client dials to reach one tier of one shard."""
    return f"{shard.service(tier)}:{TIER_PORTS[tier]}"


def shards_json(n: int) -> dict:
    """The document the gateway (and UI) consume. Environment-independent:
    the same short service names resolve under docker-compose DNS and k8s
    in-namespace DNS."""
    out = []
    for s in shards(n):
        out.append({
            "id": s.id,
            "label": s.label,
            "lo": s.lo,
            "hi": s.hi,
            "tp": gateway_host(s, "tickerplant"),
            "rdb": gateway_host(s, "rdb"),
            "idb": gateway_host(s, "idb"),
            "wdb": gateway_host(s, "wdb"),
            "hdb": gateway_host(s, "hdb"),
        })
    return {"shardCount": n, "gatewayPort": TIER_PORTS["gateway"], "shards": out}


def shard_services(n: int) -> dict[str, str]:
    """{container_name: role} for every per-shard q process, in boot order.

    Roles match what the watchdog/orchestrator expect; the gateway is added by
    callers that manage it (it isn't per-shard).
    """
    out: dict[str, str] = {}
    for s in shards(n):
        for tier in SHARD_TIERS:
            out[s.service(tier)] = tier
    return out


def healed_services(n: int) -> list[str]:
    """Every service the watchdog actively heals: all per-shard processes plus
    the gateway. Feed sims are excluded on purpose (their state is user-chosen).
    """
    return list(shard_services(n).keys()) + ["gateway"]


def managed_services(n: int) -> dict[str, str]:
    """{container_name: service_name} for the orchestrator - per-shard
    processes + gateway + feed sims. Names are identity mappings under both
    compose and k8s."""
    out = {name: name for name in shard_services(n)}
    out["gateway"] = "gateway"
    for feed in FEED_SERVICES:
        out[feed] = feed
    return out


if __name__ == "__main__":  # quick human-readable dump: python -m app.topology 4
    import json
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    print(json.dumps(shards_json(n), indent=2))
