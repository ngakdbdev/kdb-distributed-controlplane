"""
migration.py (router) - the "should we switch off our current kdb+ stack"
journey: paste/upload the client's existing q scripts for a static effort
assessment, then size and cost an equivalent TickHouse here for comparison.

Stateless by design for this first cut - nothing here is persisted (no
MigrationAssessment table). A client's source code is sensitive; not writing
it to disk/DB unless there's an explicit reason to keeps the exposure surface
smaller. If this becomes a repeat-visit workflow later, persistence is a
natural follow-on, not a redesign.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import migration_analyzer as ma
from .. import tco as tco_mod
from .. import tickhouse as th
from .auth import CurrentUser, require_tenant_scope

router = APIRouter(prefix="/migration", tags=["migration"])


class ScriptFile(BaseModel):
    name: str
    content: str


class AnalyzeBody(BaseModel):
    files: list[ScriptFile]


@router.post("/analyze")
def analyze(body: AnalyzeBody, user: CurrentUser = Depends(require_tenant_scope)):
    if not body.files:
        raise HTTPException(status_code=400, detail="no files given")
    return ma.analyze([f.model_dump() for f in body.files])


@router.get("/tco/rates")
def default_rates(user: CurrentUser = Depends(require_tenant_scope)):
    return {"cloud_hourly_usd": tco_mod.CLOUD_HOURLY_USD,
            "onprem_hourly_per_vcpu_usd": tco_mod.ONPREM_HOURLY_PER_VCPU_USD,
            "disclaimer": "Illustrative public on-demand list-price defaults - not a quote. Edit before "
                          "using in a real proposal; verify against your target region/commitment."}


class TcoBody(BaseModel):
    location: str
    profile: str
    shard_ranges: str = "a-m, n-z"
    os: str = "ubuntu-22.04"
    idb: bool = False
    rates_override: dict | None = None
    current_annual_cost: float | None = None


@router.post("/tco")
def tco(body: TcoBody, user: CurrentUser = Depends(require_tenant_scope)):
    try:
        spec = th.auto_spec("tco-estimate", body.location, body.os, body.profile,
                            shard_ranges=body.shard_ranges, idb=body.idb)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return tco_mod.estimate(spec, rates_override=body.rates_override,
                            current_annual_cost=body.current_annual_cost)
