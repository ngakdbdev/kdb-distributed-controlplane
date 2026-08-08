"""Tests for the TCO/infra-cost estimator (app/tco.py)."""
import pytest

from app import tickhouse as th
from app import tco


def _spec(cloud="aws", shards="a-m, n-z", profile="high-throughput"):
    return th.auto_spec("acme", cloud, "ubuntu-22.04", profile, shards)


def test_estimate_produces_one_line_per_component_scaled_by_shard_count():
    spec = _spec(shards="a-m, n-z")   # 2 shards
    result = tco.estimate(spec)
    per_shard = [l for l in result["lines"] if l["component"] != "gateway"]
    assert all(l["count"] == 2 for l in per_shard)
    gateway = next(l for l in result["lines"] if l["component"] == "gateway")
    assert gateway["count"] == 1   # cluster-wide, not per-shard

def test_estimate_monthly_total_matches_sum_of_lines():
    spec = _spec()
    result = tco.estimate(spec)
    assert result["monthly_infra_usd"] == pytest.approx(sum(l["monthly_usd"] for l in result["lines"]), abs=0.01)
    assert result["annual_infra_usd"] == pytest.approx(result["monthly_infra_usd"] * 12, abs=0.1)

def test_estimate_without_current_cost_omits_savings():
    result = tco.estimate(_spec())
    assert "estimated_annual_savings_usd" not in result

def test_estimate_with_current_cost_computes_savings():
    result = tco.estimate(_spec(), current_annual_cost=100000)
    assert result["current_annual_cost_usd"] == 100000
    assert result["estimated_annual_savings_usd"] == pytest.approx(100000 - result["annual_infra_usd"], abs=0.1)

def test_rates_override_changes_the_line_and_total():
    spec = _spec(shards="a-z")  # 1 shard
    baseline = tco.estimate(spec)
    tp_line = next(l for l in baseline["lines"] if l["component"] == "tickerplant")
    overridden = tco.estimate(spec, rates_override={tp_line["instance_type"]: 999.0})
    tp_line2 = next(l for l in overridden["lines"] if l["component"] == "tickerplant")
    assert tp_line2["hourly_usd"] == 999.0
    assert overridden["monthly_infra_usd"] > baseline["monthly_infra_usd"]

def test_onprem_uses_vcpu_amortized_rate_not_missing_instance_type():
    spec = _spec(cloud="onprem")
    result = tco.estimate(spec)
    assert all(l["instance_type"] == "" for l in result["lines"])
    assert all(l["rate_source"].startswith("onprem") for l in result["lines"])
    assert result["monthly_infra_usd"] > 0

def test_every_recommended_instance_type_has_a_default_rate():
    """Every (profile, cloud) combination tickhouse.py can recommend should
    resolve to a real rate, not silently fall back to 0 - a $0 line item in
    a client-facing estimate would be a worse bug than no rate at all."""
    for profile in th.PROFILES:
        for cloud in ("aws", "azure", "gcp"):
            spec = th.auto_spec("acme", cloud, "ubuntu-22.04", profile, "a-z")
            result = tco.estimate(spec)
            zero_rate = [l for l in result["lines"] if l["rate_source"] == "no rate configured"]
            assert zero_rate == [], f"{profile}/{cloud} missing rates: {zero_rate}"
