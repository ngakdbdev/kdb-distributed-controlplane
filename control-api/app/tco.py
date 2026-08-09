"""
tco.py - infrastructure cost estimate for a TickHouse spec, and an optional
comparison against a client's current kdb+ licensing spend.

Two deliberately different confidence levels live in this file:

1. The HARDWARE FOOTPRINT (which instance types, how many) reuses
   tickhouse.py's auto_hardware - the same sizing logic that already drives
   real provisioning, so it's consistent with what would actually get
   deployed, not a separate guess invented just for this calculator.

2. The DOLLAR RATES (CLOUD_HOURLY_USD below) are illustrative public
   on-demand list-price estimates, not looked up live and not a quote - cloud
   pricing varies by region, changes over time, and real deals use
   reserved/committed pricing far below on-demand. They're editable (the API
   takes a `rates_override`) specifically so this is never presented as an
   authoritative number; the UI must keep the disclaimer visible.

The client's CURRENT license cost is never fabricated - it's a required input
the client supplies (`current_annual_cost`), because that number is
confidential/deal-specific and this tool has no way to know it.
"""
from __future__ import annotations

from . import tickhouse as th

HOURS_PER_MONTH = 24 * 30

# Illustrative on-demand hourly USD, one entry per instance type tickhouse.py's
# auto_hardware() can recommend. See module docstring - not a quote.
CLOUD_HOURLY_USD = {
    # AWS
    "m7i.2xlarge": 0.40, "i4i.2xlarge": 0.54, "r7i.4xlarge": 1.08,
    "r7i.2xlarge": 0.54, "i4i.4xlarge": 1.08, "c7i.2xlarge": 0.36,
    "c7gn.2xlarge": 0.41,
    # Azure
    "Standard_E8s_v5": 0.50, "Standard_L8s_v3": 0.58, "Standard_E16s_v5": 1.01,
    "Standard_F8s_v2": 0.34, "Standard_L16s_v3": 1.15,
    # GCP
    "n2-highmem-8": 0.58, "n2-standard-8": 0.39, "n2-highmem-16": 1.16,
    "c3-highcpu-8": 0.34, "n2-standard-16": 0.78,
}

# On-prem has no instance_type (bare-metal sizing only) - approximate an
# amortized $/vCPU-hour for owned/colo hardware instead, also editable.
ONPREM_HOURLY_PER_VCPU_USD = 0.02


def _instance_footprint(spec: th.TickHouseSpec) -> list:
    """One row per component instance actually deployed: per-shard components
    multiply by shard count, cluster-wide ones (gateway) don't."""
    rows = []
    n_shards = len(spec.shards)
    for comp in spec.components:
        if comp.hardware is None:
            continue
        count = n_shards if comp.per_shard else 1
        rows.append({"component": comp.type, "instance_type": comp.hardware.instance_type,
                    "vcpus": comp.hardware.vcpus, "count": count})
    return rows


def estimate(spec: th.TickHouseSpec, rates_override: dict | None = None,
            current_annual_cost: float | None = None) -> dict:
    rates = {**CLOUD_HOURLY_USD, **(rates_override or {})}
    footprint = _instance_footprint(spec)

    lines = []
    monthly_total = 0.0
    for row in footprint:
        if spec.location == "onprem" or not row["instance_type"]:
            hourly = row["vcpus"] * ONPREM_HOURLY_PER_VCPU_USD
            rate_source = "onprem (amortized $/vCPU-hr estimate)"
        else:
            hourly = rates.get(row["instance_type"])
            rate_source = "illustrative on-demand list price" if hourly is not None else "no rate configured"
            hourly = hourly or 0.0
        monthly = hourly * row["count"] * HOURS_PER_MONTH
        monthly_total += monthly
        lines.append({**row, "hourly_usd": round(hourly, 4), "monthly_usd": round(monthly, 2),
                      "rate_source": rate_source})

    annual_total = monthly_total * 12
    result = {
        "location": spec.location, "profile": spec.profile, "shard_count": len(spec.shards),
        "lines": lines,
        "monthly_infra_usd": round(monthly_total, 2),
        "annual_infra_usd": round(annual_total, 2),
        "disclaimer": ("Illustrative infrastructure estimate from public on-demand list-price defaults "
                       "(or an amortized on-prem vCPU-hour rate) - not a quote. Edit the rates to match "
                       "your target region/commitment before using this in a real proposal. This is "
                       "compute only: it does not include storage beyond what's sized, networking egress, "
                       "support, or KDB-X licensing (KDB-X Community Edition is free; commercial terms are "
                       "between you and KX)."),
    }
    if current_annual_cost is not None:
        result["current_annual_cost_usd"] = round(current_annual_cost, 2)
        result["estimated_annual_savings_usd"] = round(current_annual_cost - annual_total, 2)
        result["savings_disclaimer"] = ("Savings = the license/infra figure you supplied minus this "
                                        "platform's estimated infra cost above. It does not net out "
                                        "migration effort (see the migration analyzer) or ongoing "
                                        "operational cost on either side.")
    return result
