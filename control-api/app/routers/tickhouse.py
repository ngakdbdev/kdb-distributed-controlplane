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
from .auth import CurrentUser, require_tenant_scope

router = APIRouter(prefix="/tickhouses", tags=["tickhouses"])


class HighLevelSpec(BaseModel):
    name: str
    location: str
    os: str
    profile: str
    shard_ranges: str                 # "a-d, e-h, i-j"
    idb: bool = False
    ldap_ref: str = ""
    gateway_config: dict | None = None


@router.get("/meta")
def meta(user: CurrentUser = Depends(require_tenant_scope)):
    """Enum choices for the create wizard's dropdowns."""
    return {"clouds": list(th.CLOUDS), "os_types": list(th.OS_TYPES),
            "profiles": list(th.PROFILES), "component_types": list(th.COMPONENT_TYPES),
            "required_components": list(th.REQUIRED_COMPONENTS)}


@router.post("/preview")
def preview(body: HighLevelSpec, user: CurrentUser = Depends(require_tenant_scope)):
    """Auto-tune a full spec from the high-level choices and return it with any
    validation problems - the wizard's 'auto-fill + review' step. No persistence."""
    try:
        spec = th.auto_spec(body.name, body.location, body.os, body.profile,
                            body.shard_ranges, idb=body.idb, ldap_ref=body.ldap_ref,
                            gateway_config=body.gateway_config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"spec": th.spec_to_dict(spec), "problems": th.validate_spec(spec)}


@router.post("")
def create(body: HighLevelSpec, user: CurrentUser = Depends(require_tenant_scope),
           session: Session = Depends(get_session)):
    """Persist a tick cluster definition (auto-tuned from the high-level choices)."""
    try:
        spec = th.auto_spec(body.name, body.location, body.os, body.profile,
                            body.shard_ranges, idb=body.idb, ldap_ref=body.ldap_ref,
                            gateway_config=body.gateway_config)
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
    return _to_api(row)


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
def delete_tickhouse(th_id: int, user: CurrentUser = Depends(require_tenant_scope),
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
              user: CurrentUser = Depends(require_tenant_scope),
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
