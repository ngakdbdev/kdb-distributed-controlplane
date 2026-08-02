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

def test_auto_spec_fills_all_components():
    spec = th.auto_spec("acme-emea", "aws", "ubuntu-22.04", "low-latency", "a-m, n-z")
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
                                       {"label": "india", "symbols": ["RELIANCE", "TCS"]}])
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
