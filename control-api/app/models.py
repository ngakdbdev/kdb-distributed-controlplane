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
    # empty for SSO-provisioned users - they have no local password, so
    # password login is refused (verify_password("") is always False)
    password_hash: str = ""
    role: UserRole = UserRole.tenant_admin
    active: bool = True
    auth_provider: str = "local"                 # "local" / "entra"
    external_id: Optional[str] = None            # Entra object id (oid), for SSO users
    can_trade: bool = False                       # may place orders in the trading terminal
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TenantIdP(SQLModel, table=True):
    """Per-tenant SSO / identity-provider config.

    Each customer (bank) federates against THEIR OWN Microsoft Entra tenant, so
    the config is per-tenant, not global. Users are provisioned just-in-time on
    first successful login, and their role is derived from their Entra group /
    app-role membership via group_role_map. Local password login remains for
    the platform admin and for break-glass tenant admins.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True, unique=True)
    provider: str = "entra"                       # "entra" / generic "oidc"
    # OIDC issuer/authority. For Entra: https://login.microsoftonline.com/<aad-tenant-guid>/v2.0
    authority: str = ""
    client_id: str = ""
    # NOTE: stored here for the MVP, but NEVER returned by the API (write-only).
    # For production move this to a secret store (or use Entra certificate /
    # workload-identity-federation credentials, which need no stored secret).
    client_secret: str = ""
    allowed_domains: str = ""                     # comma-separated email domains, "" = any
    group_role_map: str = "{}"                    # JSON {entra_group_or_role_id: "tenant_admin"}
    default_role: UserRole = UserRole.tenant_admin
    enabled: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TenantLDAP(SQLModel, table=True):
    """Per-tenant LDAP / on-prem Active Directory config.

    For customers who authenticate against their own AD/LDAP rather than Entra.
    Two bind modes:
      * "search" (recommended for AD): bind with a read-only service account,
        search for the user by user_filter, then re-bind as the found DN with
        the user's password to verify it, reading group membership from the
        entry.
      * "direct": bind straight as bind_dn_template.format(username=...) with
        the user's password (works when the DN/UPN is derivable, e.g.
        "{username}@bank.com").
    Group membership (group_attr, default memberOf) maps to roles via
    group_role_map, matched by full group DN or by CN.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True, unique=True)
    server_uri: str = ""                          # ldaps://dc.bank.com:636 (ldaps strongly preferred)
    use_start_tls: bool = False                   # for ldap:// endpoints that upgrade via StartTLS
    bind_mode: str = "search"                     # "search" / "direct"
    # search mode
    bind_dn: str = ""                             # service-account DN
    bind_password: str = ""                       # write-only in the API
    user_search_base: str = ""                    # e.g. OU=People,DC=bank,DC=com
    user_filter: str = "(sAMAccountName={username})"
    # direct mode
    bind_dn_template: str = "{username}"          # e.g. "{username}@bank.com" or "uid={username},ou=people,dc=bank,dc=com"
    # attribute mapping
    attr_email: str = "mail"
    attr_name: str = "displayName"
    group_attr: str = "memberOf"
    group_role_map: str = "{}"                    # JSON {group_dn_or_cn: "tenant_admin"}
    default_role: UserRole = UserRole.tenant_admin
    allowed_domains: str = ""
    enabled: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


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
    action: str                                               # "start" / "stop" / "restart" / "provision" / "deprovision"
    service: str                                              # e.g. "rdb-a-m", or "data-plane" for provision
    payload: str = "{}"                                       # JSON spec for richer commands (provision: desired topology)
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


# --------------------------------------------------------------------------- tickhouses
class TickHouse(SQLModel, table=True):
    """A declaratively-defined tick cluster: shards (letter ranges), typed
    components (feedhandler/logger/tickerplant/rdb/idb/hdb/gateway) each with a
    hardware spec, deployment location, target OS, gateway config, and LDAP
    binding. The full spec is stored as JSON; provisioning queues it to the
    tenant's agent for the chosen location, which renders it into helm/compose.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    name: str = Field(index=True)                             # "acme-emea"
    location: str = "aws"                                     # aws / azure / gcp / onprem
    os: str = "ubuntu-22.04"
    profile: str = "balanced"                                 # high-throughput / low-latency / balanced
    spec_json: str = "{}"                                     # full TickHouseSpec (see app.tickhouse)
    status: str = "defined"                                   # defined / provisioning / running / failed
    agent_id: Optional[int] = Field(default=None, foreign_key="agent.id")
    last_command_id: Optional[int] = Field(default=None, foreign_key="command.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


# --------------------------------------------------------------------------- trading
class Order(SQLModel, table=True):
    """A trading order. In paper mode it's filled by the simulated OMS at the
    reference/limit price; a real broker route is a configured seam. route
    records how it was handled ('paper' / 'broker')."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    user_email: str = Field(index=True)
    symbol: str = Field(index=True)
    side: str                                                  # "buy" / "sell"
    qty: float
    order_type: str = "market"                                # "market" / "limit"
    limit_price: Optional[float] = None
    status: str = "new"                                        # new / filled / rejected / cancelled
    route: str = "paper"
    fill_price: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Position(SQLModel, table=True):
    """A tenant's net position in a symbol (weighted-average cost), maintained
    as orders fill. realized_pnl accumulates as fills reduce/flip the position."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    symbol: str = Field(index=True)
    qty: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# --------------------------------------------------------------------------- llm config
class LLMConfig(SQLModel, table=True):
    """Runtime-editable override for the natural-language-to-q / code-gen LLM
    provider (see app/llm_provider.py, app/llm_runtime_config.py). Single row
    (id=1) - this is a control-plane-wide setting, not per-tenant, set via
    the platform admin's Model Settings page instead of editing NL2Q_LLM_*
    env vars and restarting the container. Until a platform admin saves a
    row here, app/llm_runtime_config.py falls back to those env vars."""
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = "none"          # none | anthropic | openai_compatible
    model: str = ""
    api_key: str = ""               # never returned by the API - see routers/llm_config.py
    base_url: str = ""
    timeout_sec: float = 20.0
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: str = ""
