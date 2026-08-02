"""
tickhouse.py - the declarative model of a tick cluster ("TickHouse"), plus the
hardware auto-tuning that fills each component's spec for a throughput or
latency profile. Pure and heavily tested; the control-API stores/serves these
specs and the fleet agent renders them into helm/compose.

A TickHouse is: a name, a deployment location (aws/azure/gcp/onprem), a target
Linux OS, an ordered shard array of letter ranges (a-d, e-h, ...) that drives
the gateway's existing lo/hi routing, and a set of typed components
(feedhandler, high-speed logger, tickerplant, rdb, idb(optional), hdb, gateway),
each with a hardware spec. Plus gateway config, an LDAP binding reference, and
the product licence key.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

CLOUDS = ("aws", "azure", "gcp", "onprem")
OS_TYPES = ("ubuntu-22.04", "ubuntu-24.04", "rhel-9", "rocky-9", "amazonlinux-2023")
PROFILES = ("high-throughput", "low-latency", "balanced")
COMPONENT_TYPES = ("feedhandler", "logger", "tickerplant", "rdb", "idb", "hdb", "gateway")
REQUIRED_COMPONENTS = ("tickerplant", "rdb", "hdb", "gateway")


# --------------------------------------------------------------------------- #
# hardware presets: (profile, cloud) -> per-component instance/tuning template
# --------------------------------------------------------------------------- #
# instance families chosen for the profile: high-throughput favours memory +
# network bandwidth; low-latency favours per-core clock + kernel-bypass.
_INSTANCE = {
    ("high-throughput", "aws"):    {"tickerplant": "m7i.2xlarge", "logger": "i4i.2xlarge",
                                    "rdb": "r7i.4xlarge", "idb": "r7i.2xlarge",
                                    "hdb": "i4i.4xlarge", "gateway": "c7i.2xlarge",
                                    "feedhandler": "c7i.2xlarge"},
    ("low-latency", "aws"):        {"tickerplant": "c7i.2xlarge", "logger": "i4i.2xlarge",
                                    "rdb": "r7i.2xlarge", "idb": "r7i.2xlarge",
                                    "hdb": "i4i.2xlarge", "gateway": "c7i.2xlarge",
                                    "feedhandler": "c7gn.2xlarge"},
    ("high-throughput", "azure"):  {"tickerplant": "Standard_E8s_v5", "logger": "Standard_L8s_v3",
                                    "rdb": "Standard_E16s_v5", "idb": "Standard_E8s_v5",
                                    "hdb": "Standard_L16s_v3", "gateway": "Standard_F8s_v2",
                                    "feedhandler": "Standard_F8s_v2"},
    ("low-latency", "azure"):      {"tickerplant": "Standard_F8s_v2", "logger": "Standard_L8s_v3",
                                    "rdb": "Standard_E8s_v5", "idb": "Standard_E8s_v5",
                                    "hdb": "Standard_L8s_v3", "gateway": "Standard_F8s_v2",
                                    "feedhandler": "Standard_F8s_v2"},
    ("high-throughput", "gcp"):    {"tickerplant": "n2-highmem-8", "logger": "n2-standard-8",
                                    "rdb": "n2-highmem-16", "idb": "n2-highmem-8",
                                    "hdb": "n2-standard-16", "gateway": "c3-highcpu-8",
                                    "feedhandler": "c3-highcpu-8"},
    ("low-latency", "gcp"):        {"tickerplant": "c3-highcpu-8", "logger": "n2-standard-8",
                                    "rdb": "n2-highmem-8", "idb": "n2-highmem-8",
                                    "hdb": "n2-standard-8", "gateway": "c3-highcpu-8",
                                    "feedhandler": "c3-highcpu-8"},
}
# per-component base sizing (vcpus, memory_gb, disk_gb, disk_tier)
_SIZING = {
    "feedhandler": (8, 16, 100, "ssd"),
    "logger":      (8, 32, 500, "nvme"),     # high-speed log destination -> NVMe
    "tickerplant": (8, 32, 200, "nvme"),
    "rdb":         (16, 128, 200, "ssd"),    # in-memory today's data
    "idb":         (8, 64, 500, "ssd"),
    "hdb":         (8, 32, 2000, "nvme"),    # historical on disk -> big fast disk
    "gateway":     (8, 16, 100, "ssd"),
}
# throughput bumps memory/disk; latency keeps cores hot and disks fast but lean
_PROFILE_MULT = {
    "high-throughput": {"memory_gb": 2.0, "disk_gb": 2.0},
    "low-latency":     {"memory_gb": 1.0, "disk_gb": 1.0},
    "balanced":        {"memory_gb": 1.0, "disk_gb": 1.0},
}
_PROFILE_TUNING = {
    "high-throughput": ["batch-publish", "large-tcp-buffers", "numa-balanced", "transparent-hugepages"],
    "low-latency":     ["cpu-pinning", "core-isolation", "hugepages", "kernel-bypass-nic",
                        "busy-poll", "disable-cstates"],
    "balanced":        ["large-tcp-buffers"],
}


@dataclass
class HardwareSpec:
    cloud: str
    os: str
    instance_type: str          # "" for onprem (bare-metal sizing only)
    vcpus: int
    memory_gb: int
    disk_gb: int
    disk_tier: str              # ssd | nvme
    nic: str                    # standard | high-bandwidth | kernel-bypass
    tuning: list = field(default_factory=list)


@dataclass
class ComponentSpec:
    type: str
    per_shard: bool = True      # one per shard (tp/rdb/idb/hdb) vs one per cluster (gateway)
    hardware: HardwareSpec | None = None


@dataclass
class ShardRange:
    label: str                  # e.g. "A-D"
    lo: str                     # "A"
    hi: str                     # "D"

    @property
    def id(self) -> str:
        return f"s{self.lo.lower()}{self.hi.lower()}"


@dataclass
class TickHouseSpec:
    name: str
    location: str               # cloud
    os: str
    profile: str
    shards: list                # list[ShardRange]
    components: list            # list[ComponentSpec]
    gateway_config: dict = field(default_factory=dict)
    ldap_ref: str = ""          # tenant LDAP binding name (optional)
    idb_enabled: bool = False


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")


def parse_shard_ranges(spec: str) -> list:
    """'a-d, e-h, i-j' -> [ShardRange(...), ...]. Also accepts single letters
    like 'a' (a-a). Raises ValueError on malformed / out-of-order ranges."""
    out = []
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        if "-" in part:
            lo, hi = [x.strip().upper() for x in part.split("-", 1)]
        else:
            lo = hi = part.strip().upper()
        if not (len(lo) == 1 and len(hi) == 1 and lo.isalpha() and hi.isalpha()):
            raise ValueError(f"bad shard range '{part}': use single letters like a-d")
        if lo > hi:
            raise ValueError(f"bad shard range '{part}': {lo} > {hi}")
        out.append(ShardRange(label=f"{lo}-{hi}", lo=lo, hi=hi))
    if not out:
        raise ValueError("no shard ranges given")
    return out


def validate_spec(spec: TickHouseSpec) -> list:
    """Return a list of human-readable problems ([] == valid)."""
    problems = []
    if not _NAME_RE.match(spec.name or ""):
        problems.append("name must be lowercase alphanumeric/hyphen, 3-40 chars, e.g. 'acme-emea'")
    if spec.location not in CLOUDS:
        problems.append(f"location must be one of {CLOUDS}")
    if spec.os not in OS_TYPES:
        problems.append(f"os must be one of {OS_TYPES}")
    if spec.profile not in PROFILES:
        problems.append(f"profile must be one of {PROFILES}")
    if not spec.shards:
        problems.append("at least one shard range is required")
    # shard ranges must not overlap
    covered = []
    for sr in sorted(spec.shards, key=lambda s: s.lo):
        rng = range(ord(sr.lo), ord(sr.hi) + 1)
        if any(c in covered for c in rng):
            problems.append(f"shard range {sr.label} overlaps another range")
        covered.extend(rng)
    have = {c.type for c in spec.components}
    for req in REQUIRED_COMPONENTS:
        if req not in have:
            problems.append(f"missing required component: {req}")
    if spec.idb_enabled and "idb" not in have:
        problems.append("idb_enabled is set but no idb component is present")
    return problems


# --------------------------------------------------------------------------- #
# hardware auto-tuning (item 4)
# --------------------------------------------------------------------------- #
def auto_hardware(profile: str, cloud: str, os: str, component: str) -> HardwareSpec:
    """Auto-initialise a component's hardware for the chosen profile/cloud/OS."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile}")
    if cloud not in CLOUDS:
        raise ValueError(f"unknown cloud {cloud}")
    vcpus, mem, disk, tier = _SIZING[component]
    mult = _PROFILE_MULT[profile]
    mem = int(mem * mult.get("memory_gb", 1.0))
    disk = int(disk * mult.get("disk_gb", 1.0))
    key = (profile if profile != "balanced" else "high-throughput", cloud)
    instance = "" if cloud == "onprem" else _INSTANCE.get(key, {}).get(component, "")
    nic = ("kernel-bypass" if profile == "low-latency" and component in ("feedhandler", "tickerplant")
           else "high-bandwidth" if profile == "high-throughput" else "standard")
    return HardwareSpec(cloud=cloud, os=os, instance_type=instance, vcpus=vcpus,
                        memory_gb=mem, disk_gb=disk, disk_tier=tier, nic=nic,
                        tuning=list(_PROFILE_TUNING[profile]))


def auto_spec(name: str, location: str, os: str, profile: str,
              shard_ranges: str, idb: bool = False,
              ldap_ref: str = "", gateway_config: dict | None = None) -> TickHouseSpec:
    """Build a fully auto-tuned TickHouseSpec from the high-level choices - the
    'reduce admin overhead' path: you pick name/location/os/profile/shards and
    every component's hardware is filled in for you."""
    shards = parse_shard_ranges(shard_ranges)
    comp_types = ["feedhandler", "logger", "tickerplant", "rdb", "hdb", "gateway"]
    if idb:
        comp_types.insert(comp_types.index("hdb"), "idb")
    components = []
    for ct in comp_types:
        components.append(ComponentSpec(
            type=ct, per_shard=(ct != "gateway"),
            hardware=auto_hardware(profile, location, os, ct)))
    return TickHouseSpec(name=name, location=location, os=os, profile=profile,
                         shards=shards, components=components,
                         gateway_config=gateway_config or {"port": 5050},
                         ldap_ref=ldap_ref, idb_enabled=idb)


# --------------------------------------------------------------------------- #
# serialization
# --------------------------------------------------------------------------- #
def spec_to_dict(spec: TickHouseSpec) -> dict:
    d = asdict(spec)
    d["shards"] = [{"id": sr.id, "label": sr.label, "lo": sr.lo, "hi": sr.hi} for sr in spec.shards]
    return d


def spec_from_dict(d: dict) -> TickHouseSpec:
    shards = [ShardRange(label=s.get("label", f"{s['lo']}-{s['hi']}"), lo=s["lo"], hi=s["hi"])
              for s in d.get("shards", [])]
    components = []
    for c in d.get("components", []):
        hw = c.get("hardware")
        components.append(ComponentSpec(type=c["type"], per_shard=c.get("per_shard", True),
                                        hardware=HardwareSpec(**hw) if hw else None))
    return TickHouseSpec(
        name=d["name"], location=d["location"], os=d["os"], profile=d["profile"],
        shards=shards, components=components,
        gateway_config=d.get("gateway_config", {}), ldap_ref=d.get("ldap_ref", ""),
        idb_enabled=d.get("idb_enabled", False))


def to_provision_payload(spec: TickHouseSpec) -> dict:
    """Canonical desired-state the fleet agent renders into helm/compose. Shard
    ids/lo/hi match what the gateway already expects in shards.json."""
    return {
        "tickhouse": spec.name,
        "location": spec.location,
        "os": spec.os,
        "profile": spec.profile,
        "shardCount": len(spec.shards),
        "shards": [{"id": sr.id, "label": sr.label, "lo": sr.lo, "hi": sr.hi} for sr in spec.shards],
        "components": [
            {"type": c.type, "per_shard": c.per_shard,
             "hardware": asdict(c.hardware) if c.hardware else None}
            for c in spec.components
        ],
        "gateway_config": spec.gateway_config,
        "ldap_ref": spec.ldap_ref,
        "idb_enabled": spec.idb_enabled,
    }
