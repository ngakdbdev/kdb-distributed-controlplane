"""
tickhouse.py (router) - the admin journey for defining and provisioning tick
clusters. Reduces admin overhead: pick name/location/os/profile/shards, get a
fully auto-tuned spec back, review it, and provision it end-to-end via the
tenant's agent - no SSH, full visibility of the architecture and its status.
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import tickhouse as th
from ..db import get_session, log_event
from ..models import Agent, Command, TickHouse
from .auth import CurrentUser, require_admin, require_tenant_scope
from .feedhandlers import build_feed_handler_row

router = APIRouter(prefix="/tickhouses", tags=["tickhouses"])


class FeedHandlerAttachment(BaseModel):
    """Shape mirrors routers.feedhandlers.FeedHandlerIn minus tickhouse_id
    (this TickHouse's own id, which doesn't exist yet at request time -
    it's filled in after the TickHouse row is created below)."""
    provider: str
    feed: str
    display_name: str = ""
    enabled: bool = False
    config: dict = {}
    secrets: dict = {}


class HighLevelSpec(BaseModel):
    name: str
    location: str
    os: str
    profile: str
    shard_ranges: str = ""            # letter-range policy: "a-d, e-h, i-j"
    sharding_policy: str = "letter-range"
    shard_symbols: list | None = None  # explicit policy: [{"label","symbols":[...]}]
    idb: bool = False
    ldap_ref: str = ""
    gateway_config: dict | None = None
    target_config: dict | None = None  # cloud/k8s coordinates (non-secret)
    feed_handler: FeedHandlerAttachment | None = None  # optional - activate a market-data source into this TickHouse in the same step


def _build(body: "HighLevelSpec"):
    return th.auto_spec(body.name, body.location, body.os, body.profile,
                        shard_ranges=body.shard_ranges, idb=body.idb, ldap_ref=body.ldap_ref,
                        gateway_config=body.gateway_config, sharding_policy=body.sharding_policy,
                        shard_symbols=body.shard_symbols, target_config=body.target_config)


@router.get("/meta")
def meta(user: CurrentUser = Depends(require_tenant_scope)):
    """Enum choices + per-cloud config fields for the create wizard."""
    return {"clouds": list(th.CLOUDS), "os_types": list(th.OS_TYPES),
            "profiles": list(th.PROFILES), "component_types": list(th.COMPONENT_TYPES),
            "required_components": list(th.REQUIRED_COMPONENTS),
            "sharding_policies": list(th.SHARDING_POLICIES),
            "config_fields": {c: th.config_fields(c) for c in th.CLOUDS}}


class SuggestBody(BaseModel):
    tick_to_trade_target_ms: float | None = None
    peak_msgs_per_sec: int | None = None
    symbol_count: int | None = None
    cross_region: bool = False


@router.post("/suggest")
def suggest(body: SuggestBody, user: CurrentUser = Depends(require_tenant_scope)):
    """SLA-driven starting point: 'my tick-to-trade target is 100ms and I
    expect 8000 msgs/sec peak' -> a suggested profile, shard count, and an
    estimated (explicitly caveated) latency budget breakdown. Purely
    advisory and stateless - nothing is persisted here. The wizard pre-fills
    /preview's profile/shard_ranges fields from this response so the operator
    reviews and can override before /preview -> /create -> /provision, the
    same explicit multi-step approval this wizard already requires for every
    other path - this endpoint doesn't add a way to skip that."""
    return th.suggest_blueprint(
        tick_to_trade_target_ms=body.tick_to_trade_target_ms,
        peak_msgs_per_sec=body.peak_msgs_per_sec,
        symbol_count=body.symbol_count,
        cross_region=body.cross_region,
    )


@router.post("/preview")
def preview(body: HighLevelSpec, user: CurrentUser = Depends(require_tenant_scope)):
    """Auto-tune a full spec from the high-level choices and return it with any
    validation problems - the wizard's 'auto-fill + review' step. No persistence."""
    try:
        spec = _build(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"spec": th.spec_to_dict(spec), "problems": th.validate_spec(spec)}


@router.post("")
def create(body: HighLevelSpec, user: CurrentUser = Depends(require_admin),
           session: Session = Depends(get_session)):
    """Persist a tick cluster definition (auto-tuned from the high-level choices).
    Admin-only (require_admin) - defining infrastructure is a provisioning
    action, not something a functional_user/quant_analyst needs day-to-day."""
    try:
        spec = _build(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    problems = th.validate_spec(spec)
    if problems:
        raise HTTPException(status_code=400, detail={"problems": problems})

    row = TickHouse(tenant_id=user.tenant_id, name=spec.name, location=spec.location,
                    os=spec.os, profile=spec.profile,
                    spec_json=json.dumps(th.spec_to_dict(spec)), status="defined")
    session.add(row)
    session.commit()
    session.refresh(row)
    log_event(session, user.email, "tickhouse_created", spec.name,
              detail=f"{spec.location}/{spec.profile}/{len(spec.shards)} shards",
              tenant_id=user.tenant_id)

    result = _to_api(row)
    if body.feed_handler is not None:
        # Same validation (known provider/feed, required credentials,
        # tenant ownership) POST /feedhandlers itself uses - see
        # build_feed_handler_row's own docstring on why this is shared
        # rather than reimplemented here. A bad feed_handler block fails
        # the WHOLE request (the TickHouse row above isn't rolled back by
        # this exception, matching this codebase's other multi-step
        # creation flows where an already-committed prerequisite step
        # stands even if a later step 400s - the operator retries just the
        # feed handler attachment via PUT/POST /feedhandlers afterward,
        # same as any other "create X, wire it to Y" partial-failure case).
        fh = body.feed_handler
        fh_row = build_feed_handler_row(session, user.tenant_id, user.email, fh.provider, fh.feed,
                                        fh.display_name, fh.enabled, fh.config, fh.secrets, tickhouse_id=row.id)
        session.add(fh_row)
        session.commit()
        session.refresh(fh_row)
        log_event(session, user.email, "feedhandler_created", target=f"feedhandler:{fh_row.id}",
                 detail=f"{fh_row.provider}/{fh_row.feed} -> tickhouse:{row.id}", tenant_id=user.tenant_id)
        result["feed_handler"] = {
            "id": fh_row.id, "provider": fh_row.provider, "feed": fh_row.feed,
            "display_name": fh_row.display_name, "enabled": fh_row.enabled,
            "has_secrets": bool(fh_row.secrets_json),
        }
    return result


@router.get("")
def list_tickhouses(user: CurrentUser = Depends(require_tenant_scope),
                    session: Session = Depends(get_session)):
    rows = session.exec(select(TickHouse).where(TickHouse.tenant_id == user.tenant_id)).all()
    return [_to_api(r) for r in rows]


@router.get("/{th_id}")
def get_tickhouse(th_id: int, user: CurrentUser = Depends(require_tenant_scope),
                  session: Session = Depends(get_session)):
    row = _load(th_id, user, session)
    return _to_api(row, include_spec=True)


@router.delete("/{th_id}")
def delete_tickhouse(th_id: int, user: CurrentUser = Depends(require_admin),
                     session: Session = Depends(get_session)):
    row = _load(th_id, user, session)
    session.delete(row)
    session.commit()
    log_event(session, user.email, "tickhouse_deleted", row.name, tenant_id=user.tenant_id)
    return {"deleted": True}


class ProvisionBody(BaseModel):
    agent_id: int


@router.post("/{th_id}/provision")
def provision(th_id: int, body: ProvisionBody,
              user: CurrentUser = Depends(require_admin),
              session: Session = Depends(get_session)):
    """Queue the full spec to the tenant's agent for this cluster's location.
    The agent renders it into helm/compose and stands the cluster up."""
    row = _load(th_id, user, session)
    agent = session.get(Agent, body.agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="agent not found")
    if agent.environment.value != row.location:
        raise HTTPException(
            status_code=400,
            detail=f"agent is in '{agent.environment.value}' but this tickhouse targets '{row.location}'")

    spec = th.spec_from_dict(json.loads(row.spec_json))
    payload = {"desired": th.to_provision_payload(spec), "tickhouse_id": row.id}
    cmd = Command(tenant_id=user.tenant_id, agent_id=body.agent_id,
                  action="provision", service="tickhouse", payload=json.dumps(payload))
    session.add(cmd)
    session.commit()
    session.refresh(cmd)

    row.status = "provisioning"
    row.agent_id = body.agent_id
    row.last_command_id = cmd.id
    session.add(row)
    session.commit()
    log_event(session, user.email, "tickhouse_provision_requested",
              f"{row.name} ({row.location})", detail=f"agent={agent.name}", tenant_id=user.tenant_id)
    return {"tickhouse_id": row.id, "command_id": cmd.id, "status": row.status}


@router.get("/{th_id}/status")
def status(th_id: int, user: CurrentUser = Depends(require_tenant_scope),
           session: Session = Depends(get_session)):
    row = _load(th_id, user, session)
    cmd_status = None
    if row.last_command_id:
        cmd = session.get(Command, row.last_command_id)
        if cmd:
            cmd_status = {"status": cmd.status, "detail": cmd.result_detail}
            # reflect the agent's result back onto the tickhouse
            if cmd.status == "success" and row.status == "provisioning":
                row.status = "running"
                session.add(row); session.commit()
            elif cmd.status == "failure" and row.status == "provisioning":
                row.status = "failed"
                session.add(row); session.commit()
    return {"tickhouse_id": row.id, "status": row.status, "last_command": cmd_status}


# ---- helpers --------------------------------------------------------------

def _load(th_id: int, user: CurrentUser, session: Session) -> TickHouse:
    row = session.get(TickHouse, th_id)
    if row is None or row.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="tickhouse not found")
    return row


def _to_api(row: TickHouse, include_spec: bool = False) -> dict:
    out = {"id": row.id, "name": row.name, "location": row.location, "os": row.os,
           "profile": row.profile, "status": row.status, "agent_id": row.agent_id,
           "created_at": row.created_at.isoformat() if row.created_at else None}
    if include_spec:
        out["spec"] = json.loads(row.spec_json)
    return out
