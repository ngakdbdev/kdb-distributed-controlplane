import json
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session, log_event
from ..models import Agent, AgentEnvironment, AgentStatus, Command, CommandStatus
from ..security import hash_password, verify_password
from .. import topology
from .auth import CurrentUser, require_tenant_scope

router = APIRouter(prefix="/fleet", tags=["fleet"])

# an agent that hasn't heartbeat within this window is reported as offline
HEARTBEAT_OFFLINE_AFTER = timedelta(seconds=30)


# ------------------------------------------------------- tenant admin side
class CreateAgentRequest(BaseModel):
    name: str
    environment: AgentEnvironment


@router.get("/agents")
def list_agents(user: CurrentUser = Depends(require_tenant_scope),
                 session: Session = Depends(get_session)):
    agents = session.exec(select(Agent).where(Agent.tenant_id == user.tenant_id)).all()
    out = []
    for a in agents:
        status = a.status
        if status == AgentStatus.online and a.last_heartbeat and \
                datetime.utcnow() - a.last_heartbeat > HEARTBEAT_OFFLINE_AFTER:
            status = AgentStatus.offline
        d = a.model_dump()
        d["status"] = status
        d["service_status"] = json.loads(a.last_reported_status or "{}")
        out.append(d)
    return out


@router.post("/agents")
def create_agent(body: CreateAgentRequest, user: CurrentUser = Depends(require_tenant_scope),
                  session: Session = Depends(get_session)):
    """
    Registers a new agent slot and returns a one-time enrollment token. The
    tenant admin hands this token to whoever is installing the agent Helm
    chart in their own AWS/Azure/GCP/on-prem cluster - the token is
    single-use and expires the moment the agent actually enrolls.
    """
    agent = Agent(tenant_id=user.tenant_id, name=body.name, environment=body.environment)
    session.add(agent)
    session.commit()
    session.refresh(agent)
    log_event(session, user.email, "create_agent", agent.name,
              detail=f"environment={body.environment.value}", tenant_id=user.tenant_id)
    return {
        "agent_id": agent.id,
        "name": agent.name,
        "environment": agent.environment,
        "enrollment_token": agent.enrollment_token,
    }


@router.post("/agents/{agent_id}/commands")
def enqueue_command(agent_id: int, action: str, service: str,
                     user: CurrentUser = Depends(require_tenant_scope),
                     session: Session = Depends(get_session)):
    if action not in ("start", "stop", "restart"):
        raise HTTPException(status_code=400, detail="action must be start, stop, or restart")
    agent = session.get(Agent, agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="agent not found")

    cmd = Command(tenant_id=user.tenant_id, agent_id=agent_id, action=action, service=service)
    session.add(cmd)
    session.commit()
    session.refresh(cmd)
    result = cmd.model_dump()
    log_event(session, user.email, "enqueue_command", f"{agent.name}/{service}",
              detail=action, tenant_id=user.tenant_id)
    return result


# ---- provisioning: the tenant-admin picks an environment (= agent) + shard
# count from the UI, and we queue a `provision` command carrying the desired
# topology. The agent living in that environment realizes it (helm upgrade /
# compose regen) and reports back. This is the "create ticker plant
# components in AWS/Azure/GCP from the UI" path.
class ProvisionRequest(BaseModel):
    shard_count: int
    note: str = ""


def _desired_topology(shard_count: int) -> dict:
    """Canonical desired-state spec the agent reconciles to. Reuses the same
    topology module the whole system derives from, so the agent, the gateway,
    and the control plane can never disagree about what N shards means."""
    return {
        "shardCount": shard_count,
        "topology": topology.shards_json(shard_count),
        "services": sorted(topology.managed_services(shard_count).keys()),
    }


def _load_agent_for_tenant(agent_id: int, user: CurrentUser, session: Session) -> Agent:
    agent = session.get(Agent, agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent


@router.post("/agents/{agent_id}/provision")
def provision(agent_id: int, body: ProvisionRequest,
              user: CurrentUser = Depends(require_tenant_scope),
              session: Session = Depends(get_session)):
    if not (1 <= body.shard_count <= topology.MAX_SHARDS):
        raise HTTPException(status_code=400,
                            detail=f"shard_count must be between 1 and {topology.MAX_SHARDS}")
    agent = _load_agent_for_tenant(agent_id, user, session)

    payload = {"desired": _desired_topology(body.shard_count), "note": body.note}
    cmd = Command(tenant_id=user.tenant_id, agent_id=agent_id,
                  action="provision", service="data-plane",
                  payload=json.dumps(payload))
    session.add(cmd)
    session.commit()
    session.refresh(cmd)
    result = cmd.model_dump()
    log_event(session, user.email, "provision_requested",
              f"{agent.name} ({agent.environment.value})",
              detail=f"shards={body.shard_count}", tenant_id=user.tenant_id)
    return result


@router.post("/agents/{agent_id}/deprovision")
def deprovision(agent_id: int,
                user: CurrentUser = Depends(require_tenant_scope),
                session: Session = Depends(get_session)):
    """Tear the data plane down in this environment (agent uninstalls the
    release / brings the compose stack down). Symmetric with provision."""
    agent = _load_agent_for_tenant(agent_id, user, session)
    cmd = Command(tenant_id=user.tenant_id, agent_id=agent_id,
                  action="deprovision", service="data-plane", payload="{}")
    session.add(cmd)
    session.commit()
    session.refresh(cmd)
    result = cmd.model_dump()
    log_event(session, user.email, "deprovision_requested",
              f"{agent.name} ({agent.environment.value})", tenant_id=user.tenant_id)
    return result


@router.get("/agents/{agent_id}/commands")
def list_agent_commands(agent_id: int, limit: int = 50,
                        user: CurrentUser = Depends(require_tenant_scope),
                        session: Session = Depends(get_session)):
    """Recent commands for one agent, newest first - the UI polls this to show
    provisioning job status (queued -> dispatched -> success/failure)."""
    _load_agent_for_tenant(agent_id, user, session)
    cmds = session.exec(
        select(Command)
        .where(Command.agent_id == agent_id, Command.tenant_id == user.tenant_id)
        .order_by(Command.id.desc())
        .limit(min(limit, 200))
    ).all()
    return [c.model_dump() for c in cmds]


# ------------------------------------------------------------------ agent side
# Everything below is called by the agent process itself, not by a logged-in
# user - auth is the agent's own secret, never a user JWT.

class EnrollRequest(BaseModel):
    enrollment_token: str


@router.post("/enroll")
def enroll(body: EnrollRequest, session: Session = Depends(get_session)):
    agent = session.exec(select(Agent).where(Agent.enrollment_token == body.enrollment_token)).first()
    if agent is None:
        raise HTTPException(status_code=404, detail="invalid or already-used enrollment token")
    if agent.agent_secret_hash is not None:
        raise HTTPException(status_code=409, detail="this agent has already enrolled")

    agent_secret = f"agentsecret_{secrets.token_urlsafe(32)}"
    agent.agent_secret_hash = hash_password(agent_secret)
    agent.status = AgentStatus.online
    agent.last_heartbeat = datetime.utcnow()
    session.add(agent)
    session.commit()
    log_event(session, f"agent:{agent.id}", "agent_enrolled", agent.name, tenant_id=agent.tenant_id)

    return {"agent_id": agent.id, "agent_secret": agent_secret}


def _authenticate_agent(agent_id: int, x_agent_secret: str, session: Session) -> Agent:
    agent = session.get(Agent, agent_id)
    if agent is None or agent.agent_secret_hash is None:
        raise HTTPException(status_code=401, detail="unknown agent")
    if agent.status == AgentStatus.revoked:
        raise HTTPException(status_code=403, detail="agent has been revoked")
    if not verify_password(x_agent_secret, agent.agent_secret_hash):
        raise HTTPException(status_code=401, detail="invalid agent secret")
    return agent


class HeartbeatRequest(BaseModel):
    service_status: dict = {}    # {"rdb-a-m": "running", ...} - this heartbeat's snapshot


@router.post("/agents/{agent_id}/heartbeat")
def heartbeat(agent_id: int, body: HeartbeatRequest,
              x_agent_secret: str = Header(default=""),
              session: Session = Depends(get_session)):
    agent = _authenticate_agent(agent_id, x_agent_secret, session)

    agent.status = AgentStatus.online
    agent.last_heartbeat = datetime.utcnow()
    agent.last_reported_status = json.dumps(body.service_status)
    session.add(agent)

    # pull up to 10 queued commands and mark them dispatched in the same
    # pass, so a crashed agent that never acks doesn't cause the same
    # command to be silently re-sent forever without visibility
    queued = session.exec(
        select(Command)
        .where(Command.agent_id == agent_id, Command.status == CommandStatus.queued)
        .limit(10)
    ).all()
    for cmd in queued:
        cmd.status = CommandStatus.dispatched
        cmd.dispatched_at = datetime.utcnow()
        session.add(cmd)

    result = [c.model_dump() for c in queued]  # snapshot before commit expires these instances
    session.commit()
    return {"commands": result}


class CommandResultRequest(BaseModel):
    outcome: str        # "success" / "failure"
    detail: str = ""


@router.post("/agents/{agent_id}/commands/{command_id}/result")
def report_command_result(agent_id: int, command_id: int, body: CommandResultRequest,
                           x_agent_secret: str = Header(default=""),
                           session: Session = Depends(get_session)):
    agent = _authenticate_agent(agent_id, x_agent_secret, session)
    cmd = session.get(Command, command_id)
    if cmd is None or cmd.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="command not found")

    cmd.status = CommandStatus.success if body.outcome == "success" else CommandStatus.failure
    cmd.result_detail = body.detail
    cmd.completed_at = datetime.utcnow()
    session.add(cmd)
    session.commit()

    log_event(session, f"agent:{agent.id}", f"command_{body.outcome}",
              f"{agent.name}/{cmd.service}", detail=f"{cmd.action}: {body.detail}",
              tenant_id=agent.tenant_id)
    return {"acknowledged": True}
