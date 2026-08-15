"""Tests for the TickHouse spec model, shard parsing, validation, auto-tuning."""
import pytest

from app import tickhouse as th


# ---- shard range parsing --------------------------------------------------

def test_parse_shard_ranges():
    ranges = th.parse_shard_ranges("a-d, e-h, i-j")
    assert [r.label for r in ranges] == ["A-D", "E-H", "I-J"]
    assert ranges[0].lo == "A" and ranges[0].hi == "D"
    assert ranges[0].id == "sad"

def test_parse_single_letter_range():
    ranges = th.parse_shard_ranges("a, b")
    assert [(r.lo, r.hi) for r in ranges] == [("A", "A"), ("B", "B")]

@pytest.mark.parametrize("bad", ["d-a", "ab-cd", "1-3", ""])
def test_parse_rejects_bad_ranges(bad):
    with pytest.raises(ValueError):
        th.parse_shard_ranges(bad)


# ---- auto-tuning (item 4) -------------------------------------------------

def test_low_latency_tuning_pins_cores_and_uses_bypass_nic():
    hw = th.auto_hardware("low-latency", "aws", "ubuntu-22.04", "tickerplant")
    assert "cpu-pinning" in hw.tuning and "hugepages" in hw.tuning
    assert hw.nic == "kernel-bypass"
    assert hw.instance_type == "c7i.2xlarge"      # high-clock family

def test_high_throughput_bumps_memory_and_disk():
    tp_lat = th.auto_hardware("low-latency", "aws", "ubuntu-22.04", "rdb")
    tp_thr = th.auto_hardware("high-throughput", "aws", "ubuntu-22.04", "rdb")
    assert tp_thr.memory_gb > tp_lat.memory_gb
    assert "batch-publish" in tp_thr.tuning

def test_hdb_gets_big_fast_disk():
    hw = th.auto_hardware("high-throughput", "aws", "ubuntu-22.04", "hdb")
    assert hw.disk_gb >= 2000 and hw.disk_tier == "nvme"

def test_logger_is_nvme_high_speed():
    hw = th.auto_hardware("low-latency", "gcp", "rhel-9", "logger")
    assert hw.disk_tier == "nvme"

def test_onprem_has_no_instance_type_but_keeps_sizing():
    hw = th.auto_hardware("high-throughput", "onprem", "rocky-9", "tickerplant")
    assert hw.instance_type == "" and hw.vcpus > 0 and hw.memory_gb > 0

def test_auto_hardware_rejects_unknown_profile():
    with pytest.raises(ValueError):
        th.auto_hardware("nonsense", "aws", "ubuntu-22.04", "hdb")


# ---- full auto spec -------------------------------------------------------

_AWS_TC = {"region": "eu-west-1", "vpc_id": "vpc-1", "subnet_ids": "subnet-1,subnet-2",
           "eks_cluster": "acme-prod", "namespace": "tick", "storage_class": "gp3",
           "ingress_class": "nginx"}


def test_auto_spec_fills_all_components():
    spec = th.auto_spec("acme-emea", "aws", "ubuntu-22.04", "low-latency", "a-m, n-z",
                        target_config=_AWS_TC)
    assert th.validate_spec(spec) == []
    types = {c.type for c in spec.components}
    assert th.REQUIRED_COMPONENTS[0] in types
    assert all(c.hardware is not None for c in spec.components)
    # gateway is one-per-cluster, tickerplant is per-shard
    gw = next(c for c in spec.components if c.type == "gateway")
    tp = next(c for c in spec.components if c.type == "tickerplant")
    assert gw.per_shard is False and tp.per_shard is True

def test_auto_spec_with_idb():
    spec = th.auto_spec("acme", "gcp", "rhel-9", "balanced", "a-z", idb=True)
    assert spec.idb_enabled and any(c.type == "idb" for c in spec.components)


# ---- validation -----------------------------------------------------------

def test_validate_flags_overlapping_shards():
    spec = th.auto_spec("acme", "aws", "ubuntu-22.04", "balanced", "a-m")
    spec.shards.append(th.ShardRange("F-Q", "F", "Q"))     # overlaps A-M
    problems = th.validate_spec(spec)
    assert any("overlap" in p for p in problems)

def test_validate_flags_missing_required_component():
    spec = th.auto_spec("acme", "aws", "ubuntu-22.04", "balanced", "a-z")
    spec.components = [c for c in spec.components if c.type != "gateway"]
    assert any("gateway" in p for p in th.validate_spec(spec))

def test_validate_flags_bad_name():
    spec = th.auto_spec("acme", "aws", "ubuntu-22.04", "balanced", "a-z")
    spec.name = "Bad Name!"
    assert any("name" in p for p in th.validate_spec(spec))

def test_default_eod_config_has_retention_policy():
    spec = th.auto_spec("acme", "aws", "ubuntu-22.04", "balanced", "a-z", target_config=_AWS_TC)
    assert spec.eod_config["rdb_retention_min"] == 2
    assert spec.eod_config["hdb_retention_days"] == 0    # keep forever by default
    assert th.validate_spec(spec) == []

def test_validate_flags_negative_rdb_retention():
    spec = th.auto_spec("acme", "aws", "ubuntu-22.04", "balanced", "a-z")
    spec.eod_config["rdb_retention_min"] = 0
    assert any("rdb_retention_min" in p for p in th.validate_spec(spec))

def test_validate_flags_negative_hdb_retention():
    spec = th.auto_spec("acme", "aws", "ubuntu-22.04", "balanced", "a-z")
    spec.eod_config["hdb_retention_days"] = -1
    assert any("hdb_retention_days" in p for p in th.validate_spec(spec))

def test_validate_allows_hdb_retention_zero_meaning_keep_forever():
    spec = th.auto_spec("acme", "aws", "ubuntu-22.04", "balanced", "a-z", target_config=_AWS_TC)
    spec.eod_config["hdb_retention_days"] = 0
    assert th.validate_spec(spec) == []


# ---- serialization round trip + provision payload ------------------------

def test_spec_dict_roundtrip():
    spec = th.auto_spec("acme-emea", "azure", "ubuntu-24.04", "low-latency", "a-d,e-h", idb=True)
    back = th.spec_from_dict(th.spec_to_dict(spec))
    assert back.name == spec.name and back.location == spec.location
    assert len(back.shards) == 2 and back.idb_enabled

def test_provision_payload_matches_gateway_shard_shape():
    spec = th.auto_spec("acme", "aws", "ubuntu-22.04", "high-throughput", "a-m, n-z")
    payload = th.to_provision_payload(spec)
    assert payload["shardCount"] == 2
    s0 = payload["shards"][0]
    assert set(s0) == {"id", "label", "lo", "hi", "symbols"}   # gateway shape + symbols
    assert payload["components"][0]["hardware"]["vcpus"] > 0


# ---- cloud / k8s target config (item: pass config for aws/gcp/azure/k8s) ----

def test_config_fields_per_cloud():
    assert "region" in th.config_fields("aws")
    assert "namespace" in th.config_fields("aws")        # k8s fields on managed clouds
    assert "project_id" in th.config_fields("gcp")
    assert "resource_group" in th.config_fields("azure")
    assert "namespace" not in th.config_fields("onprem") # compose, no k8s

def test_validate_flags_missing_cloud_config_fields():
    # aws requires region/vpc_id/subnet_ids/eks_cluster + k8s namespace/storage_class/
    # ingress_class - none supplied here, so every one of those must be flagged. A spec
    # that "validates clean" with blank cloud coordinates would only fail later, opaquely,
    # inside helm/kubectl on the agent - catch it at definition time instead.
    spec = th.auto_spec("acme", "aws", "ubuntu-22.04", "balanced", "a-z")
    problems = th.validate_spec(spec)
    for f in th.config_fields("aws"):
        assert any(f in p for p in problems), f"expected a problem mentioning {f}"

def test_validate_passes_with_full_cloud_config():
    spec = th.auto_spec("acme", "aws", "ubuntu-22.04", "balanced", "a-z", target_config=_AWS_TC)
    assert th.validate_spec(spec) == []

def test_validate_ignores_target_config_for_onprem():
    spec = th.auto_spec("acme", "onprem", "ubuntu-22.04", "balanced", "a-z",
                        target_config={"compose_project_dir": "/srv/kdb"})
    assert th.validate_spec(spec) == []

def test_target_config_flows_into_spec_and_payload():
    tc = {"region": "eu-west-1", "eks_cluster": "acme-prod", "namespace": "tick",
          "storage_class": "gp3"}
    spec = th.auto_spec("acme", "aws", "ubuntu-22.04", "low-latency", "a-z", target_config=tc)
    assert spec.target_config["namespace"] == "tick"
    payload = th.to_provision_payload(spec)
    assert payload["target_config"]["storage_class"] == "gp3"
    back = th.spec_from_dict(th.spec_to_dict(spec))
    assert back.target_config["region"] == "eu-west-1"


# ---- explicit-symbol sharding (item: user-configurable symbol shards) ----

def test_explicit_symbol_sharding():
    spec = th.auto_spec("acme", "aws", "ubuntu-22.04", "balanced", sharding_policy="explicit-symbols",
                        shard_symbols=[{"label": "mega-cap", "symbols": ["AAPL", "MSFT"]},
                                       {"label": "india", "symbols": ["RELIANCE", "TCS"]}],
                        target_config=_AWS_TC)
    assert th.validate_spec(spec) == []
    assert spec.sharding_policy == "explicit-symbols"
    assert spec.shards[0].symbols == ["AAPL", "MSFT"]
    payload = th.to_provision_payload(spec)
    assert payload["shards"][1]["symbols"] == ["RELIANCE", "TCS"]

def test_explicit_sharding_flags_duplicate_symbol():
    spec = th.auto_spec("acme", "aws", "ubuntu-22.04", "balanced", sharding_policy="explicit-symbols",
                        shard_symbols=[{"label": "a", "symbols": ["AAPL"]},
                                       {"label": "b", "symbols": ["AAPL"]}])
    assert any("assigned to both" in p for p in th.validate_spec(spec))


# ---- SLA-driven blueprint suggestion (item 4: "I need 100ms tick-to-trade") --

def test_suggest_blueprint_tight_latency_target_picks_low_latency():
    out = th.suggest_blueprint(tick_to_trade_target_ms=100)
    assert out["profile"] == "low-latency"


def test_suggest_blueprint_loose_target_with_high_volume_picks_high_throughput():
    out = th.suggest_blueprint(tick_to_trade_target_ms=5000, peak_msgs_per_sec=20000)
    assert out["profile"] == "high-throughput"


def test_suggest_blueprint_no_signals_defaults_to_balanced_and_default_shard_count():
    out = th.suggest_blueprint()
    assert out["profile"] == "balanced"
    assert out["shard_count"] == 2  # this repo's own long-standing default


def test_suggest_blueprint_high_volume_widens_shard_count():
    out = th.suggest_blueprint(peak_msgs_per_sec=15000)
    assert out["shard_count"] >= 8  # 15000 / 2000-per-shard heuristic, ceil


def test_suggest_blueprint_high_symbol_count_widens_shard_count():
    out = th.suggest_blueprint(symbol_count=6000)
    assert out["shard_count"] >= 12  # 6000 / 500-per-shard heuristic, ceil


def test_suggest_blueprint_shard_count_never_exceeds_max_shards():
    out = th.suggest_blueprint(peak_msgs_per_sec=10_000_000)
    assert out["shard_count"] == th.topology.MAX_SHARDS


def test_suggest_blueprint_latency_budget_is_a_low_high_range_per_hop():
    out = th.suggest_blueprint(tick_to_trade_target_ms=100)
    for hop, rng in out["latency_budget_ms"].items():
        assert rng["low"] <= rng["high"], hop
    assert out["latency_budget_total_ms"]["low"] <= out["latency_budget_total_ms"]["high"]


def test_suggest_blueprint_flags_target_below_optimistic_floor():
    out = th.suggest_blueprint(tick_to_trade_target_ms=0.01)
    assert out["target_likely_achievable"] is False
    assert any("BELOW even this estimate" in c for c in out["caveats"])


def test_suggest_blueprint_achievable_target_not_flagged_as_unachievable():
    out = th.suggest_blueprint(tick_to_trade_target_ms=10_000)
    assert out["target_likely_achievable"] is True


def test_suggest_blueprint_no_target_leaves_achievability_unknown():
    out = th.suggest_blueprint(peak_msgs_per_sec=1000)
    assert out["target_likely_achievable"] is None


def test_suggest_blueprint_always_caveats_that_numbers_are_estimates():
    # this is the whole point - never let the estimate be mistaken for a
    # measured guarantee (see DEMO.md's own "I don't quote a number I
    # haven't measured on your target hardware" principle)
    out = th.suggest_blueprint(tick_to_trade_target_ms=100)
    assert any("ARCHITECTURAL ESTIMATE" in c for c in out["caveats"])
    assert any("demokit.load_test" in c for c in out["caveats"])


def test_suggest_blueprint_cross_region_widens_network_hop():
    same_az = th.suggest_blueprint(tick_to_trade_target_ms=100, cross_region=False)
    cross_region = th.suggest_blueprint(tick_to_trade_target_ms=100, cross_region=True)
    assert (cross_region["latency_budget_ms"]["network_rtt"]["low"] >
            same_az["latency_budget_ms"]["network_rtt"]["low"])
