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
SHARDING_POLICIES = ("letter-range", "explicit-symbols")

# Non-secret target config the admin fills per environment when building a
# cluster. Secrets (access keys, service-principal secrets, kubeconfig tokens)
# are NEVER stored in the spec - they live in the agent's environment/secret
# store. These are the account/cluster coordinates the deploy needs.
CLOUD_CONFIG_FIELDS = {
    "aws": ["region", "vpc_id", "subnet_ids", "eks_cluster"],
    "azure": ["subscription_id", "resource_group", "location", "aks_cluster"],
    "gcp": ["project_id", "region", "gke_cluster"],
    "onprem": ["compose_project_dir"],
}
# k8s settings apply to the managed-k8s clouds (not on-prem compose)
KUBERNETES_CONFIG_FIELDS = ["namespace", "storage_class", "ingress_class"]


def config_fields(cloud: str) -> list:
    """Which target-config fields the wizard should collect for a cloud."""
    fields = list(CLOUD_CONFIG_FIELDS.get(cloud, []))
    if cloud in ("aws", "azure", "gcp"):
        fields += KUBERNETES_CONFIG_FIELDS
    return fields


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
    label: str                  # e.g. "A-D" (letter-range) or "custom-1" (explicit)
    lo: str                     # "A"  (letter-range policy)
    hi: str                     # "D"
    symbols: list = field(default_factory=list)   # explicit-symbols policy

    @property
    def id(self) -> str:
        return f"s{self.lo.lower()}{self.hi.lower()}"


DEFAULT_EOD_CONFIG = {
    "eod_hour_utc": 0, "idb_retention_days": 5,
    # rdb_retention_min: minutes of live data the chained RDB keeps in
    # memory - independent of how often wdb flushes its own buffer to disk
    # (see data-plane/q/wdb.q's .wdb.retentionIntv). hdb_retention_days: 0
    # (default) keeps sealed history forever - purging it is destructive
    # (no cold-storage archive step, just delete) and opt-in only, see
    # hdb.q's .hdb.purgeOld.
    "rdb_retention_min": 2, "hdb_retention_days": 0,
}


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
    sharding_policy: str = "letter-range"
    target_config: dict = field(default_factory=dict)   # cloud/k8s coordinates (non-secret)
    # end-of-day lifecycle: eod_hour_utc is when the trading day rolls over
    # (0 = midnight UTC; set it to an exchange's close hour instead) - the
    # tickerplant self-triggers on this, no external cron dependency.
    # idb_retention_days is how long the intraday cache keeps a day in memory
    # after wdb has sealed it into the hdb before evicting it.
    eod_config: dict = field(default_factory=lambda: dict(DEFAULT_EOD_CONFIG))


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
    if spec.sharding_policy == "explicit-symbols":
        seen = {}
        for sr in spec.shards:
            if not sr.symbols:
                problems.append(f"shard {sr.label} has no symbols")
            for sym in sr.symbols:
                if sym in seen:
                    problems.append(f"symbol {sym} is assigned to both {seen[sym]} and {sr.label}")
                seen[sym] = sr.label
    else:
        # letter ranges must not overlap
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
    if spec.location in CLOUDS:
        for f in config_fields(spec.location):
            if not str((spec.target_config or {}).get(f, "")).strip():
                problems.append(f"target_config.{f} is required for {spec.location}")
    eod_hour = spec.eod_config.get("eod_hour_utc", 0)
    if not (isinstance(eod_hour, int) and 0 <= eod_hour <= 23):
        problems.append("eod_config.eod_hour_utc must be an integer 0-23 (UTC hour the trading day rolls over)")
    retention = spec.eod_config.get("idb_retention_days", 5)
    if not (isinstance(retention, int) and retention >= 1):
        problems.append("eod_config.idb_retention_days must be a positive integer")
    rdb_retention = spec.eod_config.get("rdb_retention_min", 2)
    if not (isinstance(rdb_retention, int) and rdb_retention >= 1):
        problems.append("eod_config.rdb_retention_min must be a positive integer")
    hdb_retention = spec.eod_config.get("hdb_retention_days", 0)
    if not (isinstance(hdb_retention, int) and hdb_retention >= 0):
        problems.append("eod_config.hdb_retention_days must be 0 (keep forever) or a positive integer")
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


def parse_explicit_shards(assignments: list) -> list:
    """[{'label': 'core', 'symbols': ['AAPL','MSFT']}, ...] -> ShardRanges with
    explicit symbol membership. lo/hi are placeholders (unused by this policy);
    ids are stable by index."""
    out = []
    for i, a in enumerate(assignments or []):
        syms = [s.strip().upper() for s in (a.get("symbols") or []) if s.strip()]
        if not syms:
            raise ValueError(f"shard '{a.get('label', i)}' has no symbols")
        letter = chr(ord("A") + (i % 26))
        out.append(ShardRange(label=a.get("label") or f"shard-{i+1}",
                              lo=letter, hi=letter, symbols=syms))
    if not out:
        raise ValueError("no explicit shard assignments given")
    return out


def auto_spec(name: str, location: str, os: str, profile: str,
              shard_ranges: str = "", idb: bool = False,
              ldap_ref: str = "", gateway_config: dict | None = None,
              sharding_policy: str = "letter-range",
              shard_symbols: list | None = None,
              target_config: dict | None = None,
              eod_config: dict | None = None) -> TickHouseSpec:
    """Build a fully auto-tuned TickHouseSpec from the high-level choices - the
    'reduce admin overhead' path: pick name/location/os/profile/shards and every
    component's hardware is filled in for you. Shards are either letter-ranges
    ('a-d, e-h') or explicit symbol assignments (sharding_policy)."""
    if sharding_policy == "explicit-symbols":
        shards = parse_explicit_shards(shard_symbols or [])
    else:
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
                         ldap_ref=ldap_ref, idb_enabled=idb,
                         sharding_policy=sharding_policy,
                         target_config=target_config or {},
                         eod_config={**DEFAULT_EOD_CONFIG, **(eod_config or {})})


# --------------------------------------------------------------------------- #
# serialization
# --------------------------------------------------------------------------- #
def spec_to_dict(spec: TickHouseSpec) -> dict:
    d = asdict(spec)
    d["shards"] = [{"id": sr.id, "label": sr.label, "lo": sr.lo, "hi": sr.hi, "symbols": sr.symbols}
                   for sr in spec.shards]
    return d


def spec_from_dict(d: dict) -> TickHouseSpec:
    shards = [ShardRange(label=s.get("label", f"{s['lo']}-{s['hi']}"), lo=s["lo"], hi=s["hi"],
                         symbols=s.get("symbols", []))
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
        idb_enabled=d.get("idb_enabled", False),
        sharding_policy=d.get("sharding_policy", "letter-range"),
        target_config=d.get("target_config", {}),
        eod_config={**DEFAULT_EOD_CONFIG, **d.get("eod_config", {})})


def to_provision_payload(spec: TickHouseSpec) -> dict:
    """Canonical desired-state the fleet agent renders into helm/compose. Shard
    ids/lo/hi match what the gateway already expects in shards.json."""
    return {
        "tickhouse": spec.name,
        "location": spec.location,
        "os": spec.os,
        "profile": spec.profile,
        "shardCount": len(spec.shards),
        "sharding_policy": spec.sharding_policy,
        "shards": [{"id": sr.id, "label": sr.label, "lo": sr.lo, "hi": sr.hi,
                    "symbols": sr.symbols} for sr in spec.shards],
        "components": [
            {"type": c.type, "per_shard": c.per_shard,
             "hardware": asdict(c.hardware) if c.hardware else None}
            for c in spec.components
        ],
        "gateway_config": spec.gateway_config,
        "ldap_ref": spec.ldap_ref,
        "idb_enabled": spec.idb_enabled,
        "target_config": spec.target_config,
        "eod_config": spec.eod_config,
    }
