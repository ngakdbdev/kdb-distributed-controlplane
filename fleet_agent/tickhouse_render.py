"""
tickhouse_render.py - turn a provision payload's tickhouse spec into concrete
helm --set args (k8s) or compose env (on-prem). Pure and tested; the backends
call this and then run helm/docker.

The renderer maps each component's auto-tuned hardware onto Kubernetes resource
requests and node hints, and the shard letter-ranges onto the gateway's
existing lo/hi routing - so a declaratively-defined cluster stands up with the
sizing the admin picked, no hand-editing of values files.
"""
from __future__ import annotations


def _mem(gb) -> str:
    return f"{int(gb)}Gi"


def render_helm_sets(desired: dict) -> list:
    """helm --set args from a tickhouse spec payload (desired = to_provision_payload)."""
    sets = []
    sets.append(f"shardCount={desired.get('shardCount', 1)}")
    if desired.get("profile"):
        sets.append(f"profile={desired['profile']}")
    if desired.get("os"):
        sets.append(f"targetOS={desired['os']}")

    shards = desired.get("shards", [])
    if shards:
        if desired.get("sharding_policy") == "explicit-symbols":
            # explicit assignment: shardSymbols=s0:AAPL|MSFT;s1:GOOG|AMZN
            parts = [f"{s['id']}:{'|'.join(s.get('symbols', []))}" for s in shards]
            sets.append(f"shardSymbols={';'.join(parts)}")
        else:
            ranges = ";".join(f"{s['lo']}-{s['hi']}" for s in shards)
            sets.append(f"shardRanges={ranges}")

    # cloud / k8s target config (non-secret coordinates)
    tc = desired.get("target_config") or {}
    if tc.get("namespace"):
        sets.append(f"global.namespace={tc['namespace']}")
    if tc.get("storage_class"):
        sets.append(f"global.storageClass={tc['storage_class']}")
    if tc.get("ingress_class"):
        sets.append(f"global.ingressClass={tc['ingress_class']}")
    if tc.get("region"):
        sets.append(f"global.region={tc['region']}")

    gw = desired.get("gateway_config") or {}
    if gw.get("port"):
        sets.append(f"gateway.port={gw['port']}")

    eod = desired.get("eod_config") or {}
    if "eod_hour_utc" in eod:
        sets.append(f"eod.hourUtc={eod['eod_hour_utc']}")
    if "idb_retention_days" in eod:
        sets.append(f"idb.retentionDays={eod['idb_retention_days']}")
    if "rdb_retention_min" in eod:
        sets.append(f"rdb.retentionMin={eod['rdb_retention_min']}")
    if "hdb_retention_days" in eod:
        sets.append(f"hdb.retentionDays={eod['hdb_retention_days']}")

    for comp in desired.get("components", []):
        hw = comp.get("hardware") or {}
        t = comp["type"]
        if hw.get("vcpus"):
            sets.append(f"resources.{t}.requests.cpu={hw['vcpus']}")
            # "cpu-pinning" (the low-latency profile's default tuning tag -
            # see tickhouse.py's _PROFILE_TUNING) becomes a REAL kdb-services.yaml
            # effect here: setting limits.cpu equal to requests.cpu is what
            # makes this pod eligible for Kubernetes' Guaranteed QoS class,
            # the prerequisite for the kubelet's static CPUManager policy to
            # grant it exclusive whole-core pinning. This is the one part of
            # "cpu-pinning" that's automatic - it needs no real core numbers,
            # unlike hw.cpuset/numa_node below (which an operator sets
            # explicitly once they know their actual hardware/node-pool
            # layout; there's no generic way to derive real core numbers
            # from a profile name alone).
            if "cpu-pinning" in (hw.get("tuning") or []):
                sets.append(f"resources.{t}.limits.cpu={hw['vcpus']}")
        if hw.get("memory_gb"):
            sets.append(f"resources.{t}.requests.memory={_mem(hw['memory_gb'])}")
        if hw.get("disk_gb"):
            sets.append(f"resources.{t}.storage={hw['disk_gb']}Gi")
        if hw.get("disk_tier"):
            sets.append(f"resources.{t}.diskTier={hw['disk_tier']}")
        if hw.get("instance_type"):
            sets.append(f"nodePools.{t}.instanceType={hw['instance_type']}")
        if hw.get("nic"):
            sets.append(f"nodePools.{t}.nic={hw['nic']}")
        # NUMA-labeled node targeting (operator-set - see HardwareSpec.numa_node's
        # own docstring for why this can't be auto-derived). Maps onto the
        # chart's nodeSelectors.<type> value (kdb-services.yaml).
        if hw.get("numa_node"):
            sets.append(f"nodeSelectors.{t}.numa-node={hw['numa_node']}")
    return sets


#  TickHouseSpec component type -> gen_topology.py compose env-var prefix.
# Only rdb/hdb/tickerplant have a clean 1:1 match to a real compose service
# today: idb/gateway don't peach (pinning them buys nothing - see
# kdb-entrypoint.sh's KDB_THREADS comment) and wdb isn't a TickHouseSpec
# component at all (tickhouse.py's COMPONENT_TYPES has no "wdb" entry -
# a real gap in that model, not something faked around here), while
# "feedhandler"/"logger" don't correspond to any service gen_topology.py's
# compose path actually generates (that path uses bpipe-sim/crims-sim/
# provider feeds instead, sized independently of the TickHouse hardware
# model). Extending coverage to those needs a change to gen_topology.py and
# the TickHouseSpec component model, not just this renderer.
_COMPOSE_PIN_PREFIX = {"rdb": "RDB", "hdb": "HDB", "tickerplant": "TP"}


def render_compose_env(desired: dict) -> dict:
    """Environment overrides for the on-prem compose path (gen_topology reads
    SHARD_COUNT; the rest are advisory labels the compose template can consume).
    Also carries per-component KDB_CPUSET/KDB_NUMA_NODE overrides (see
    kdb-entrypoint.sh's numactl support and HardwareSpec.cpuset/numa_node's
    own docstring for why these are operator-set, not auto-derived) - for
    whichever components gen_topology.py's compose path actually generates a
    matching {PREFIX}_CPUSET/{PREFIX}_NUMA_NODE env var for (see
    _COMPOSE_PIN_PREFIX above)."""
    env = {"SHARD_COUNT": str(desired.get("shardCount", 1)),
           "TH_PROFILE": desired.get("profile", ""),
           "TH_OS": desired.get("os", "")}
    shards = desired.get("shards", [])
    if shards:
        env["TH_SHARD_RANGES"] = ";".join(f"{s['lo']}-{s['hi']}" for s in shards)
    for comp in desired.get("components", []):
        prefix = _COMPOSE_PIN_PREFIX.get(comp["type"])
        if not prefix:
            continue
        hw = comp.get("hardware") or {}
        if hw.get("cpuset"):
            env[f"{prefix}_CPUSET"] = hw["cpuset"]
        if hw.get("numa_node"):
            env[f"{prefix}_NUMA_NODE"] = hw["numa_node"]
    return env


def summarize(desired: dict) -> str:
    comps = ", ".join(sorted({c["type"] for c in desired.get("components", [])}))
    return (f"{desired.get('tickhouse', '?')}: {desired.get('shardCount', '?')} shards, "
            f"{desired.get('profile', '?')} profile, components: {comps}")
