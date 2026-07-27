import secrets
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


def _token(prefix: str, nbytes: int = 24) -> str:
    return f"{prefix}_{secrets.token_urlsafe(nbytes)}"


# --------------------------------------------------------------------------- tenants
class Tenant(SQLModel, table=True):
    """One customer (bank). Everything else in the system hangs off this."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    slug: str = Field(index=True, unique=True)              # url-safe id, e.g. "acme-bank"
    plan: str = "trial"                                      # "trial" / "standard" / "enterprise"
    status: str = "active"                                   # "active" / "suspended"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UserRole(str, Enum):
    platform_admin = "platform_admin"   # you - the SaaS operator, sees/manages all tenants
    tenant_admin = "tenant_admin"       # a bank's admin - sees/manages only their own tenant


class User(SQLModel, table=True):
    """A login. tenant_id is null only for platform_admin users."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[int] = Field(default=None, foreign_key="tenant.id", index=True)
    email: str = Field(index=True, unique=True)
    password_hash: str
    role: UserRole = UserRole.tenant_admin
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


# --------------------------------------------------------------------------- fleet (agents)
class AgentEnvironment(str, Enum):
    aws = "aws"
    azure = "azure"
    gcp = "gcp"
    onprem = "onprem"


class AgentStatus(str, Enum):
    pending = "pending"       # enrollment token issued, agent has not checked in yet
    online = "online"         # heartbeat received within the last window
    offline = "offline"       # heartbeat window missed
    revoked = "revoked"       # operator disabled this agent


class Agent(SQLModel, table=True):
    """
    One customer-controlled cluster (their AWS/Azure/GCP account or their
    on-prem datacenter). The SaaS control plane never has direct network
    access into this cluster - the agent living inside it calls out to us.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    name: str                                                 # "acme-bank-aws-prod"
    environment: AgentEnvironment = AgentEnvironment.aws
    status: AgentStatus = AgentStatus.pending
    enrollment_token: str = Field(default_factory=lambda: _token("enroll"), unique=True)
    agent_secret_hash: Optional[str] = None                   # set once the agent actually enrolls
    last_heartbeat: Optional[datetime] = None
    last_reported_status: str = "{}"                          # JSON blob: {"rdb-a-m": "running", ...}
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CommandStatus(str, Enum):
    queued = "queued"
    dispatched = "dispatched"   # sent to the agent on its last heartbeat, awaiting result
    success = "success"
    failure = "failure"


class Command(SQLModel, table=True):
    """
    One queued instruction for an agent (start/stop/restart a named
    service). The agent pulls these on its next heartbeat - the control
    plane never pushes to the agent, since the agent's environment is not
    expected to accept inbound connections from us.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    agent_id: int = Field(foreign_key="agent.id", index=True)
    action: str                                               # "start" / "stop" / "restart"
    service: str                                              # e.g. "rdb-a-m"
    status: CommandStatus = CommandStatus.queued
    result_detail: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    dispatched_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


# --------------------------------------------------------------------------- tenant-scoped data
class Connector(SQLModel, table=True):
    """A configured market-data source (e.g. the B-PIPE or CRIMS simulator)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    name: str = Field(index=True)                             # "bpipe-sim" / "crims-sim"
    kind: str                                                  # "equities" / "risk"
    description: str = ""
    service_name: str                                          # service/deployment name to control
    enabled: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Subscriber(SQLModel, table=True):
    """An entitlement record: who/what may subscribe to which table."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    name: str = Field(index=True)
    table: str                                                  # "trade" / "risk"
    role: str = "read-only"
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AuditEvent(SQLModel, table=True):
    """Every admin action and every notable system event, for the audit trail."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[int] = Field(default=None, foreign_key="tenant.id", index=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)
    actor: str                                                  # user email / "watchdog" / "agent:<id>"
    action: str                                                 # "start_service" / "auto_heal" / ...
    target: str = ""
    detail: str = ""
    outcome: str = "success"                                    # "success" / "failure"
