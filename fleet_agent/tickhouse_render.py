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
        ranges = ";".join(f"{s['lo']}-{s['hi']}" for s in shards)
        # helm list values are escaped with \,  -> use a single ';'-joined string
        sets.append(f"shardRanges={ranges}")

    gw = desired.get("gateway_config") or {}
    if gw.get("port"):
        sets.append(f"gateway.port={gw['port']}")

    for comp in desired.get("components", []):
        hw = comp.get("hardware") or {}
        t = comp["type"]
        if hw.get("vcpus"):
            sets.append(f"resources.{t}.requests.cpu={hw['vcpus']}")
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
    return sets


def render_compose_env(desired: dict) -> dict:
    """Environment overrides for the on-prem compose path (gen_topology reads
    SHARD_COUNT; the rest are advisory labels the compose template can consume)."""
    env = {"SHARD_COUNT": str(desired.get("shardCount", 1)),
           "TH_PROFILE": desired.get("profile", ""),
           "TH_OS": desired.get("os", "")}
    shards = desired.get("shards", [])
    if shards:
        env["TH_SHARD_RANGES"] = ";".join(f"{s['lo']}-{s['hi']}" for s in shards)
    return env


def summarize(desired: dict) -> str:
    comps = ", ".join(sorted({c["type"] for c in desired.get("components", [])}))
    return (f"{desired.get('tickhouse', '?')}: {desired.get('shardCount', '?')} shards, "
            f"{desired.get('profile', '?')} profile, components: {comps}")
