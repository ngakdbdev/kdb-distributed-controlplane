import secrets
from datetime import date, datetime
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
    tenant_admin = "tenant_admin"       # a bank's admin ("Admin" in the UI) - full access within their own tenant:
                                         # user management, TickHouses/Fleet/Infrastructure settings,
                                         # connectors, trading - everything require_admin/require_trading gate
    functional_user = "functional_user" # day-to-day trading/ops within a tenant - Markets/Orders/Portfolio/
                                         # Bot/Query, gated by the same can_trade flag as any non-admin role
                                         # (see require_trading) - no admin/infra pages
    quant_analyst = "quant_analyst"     # research/analysis within a tenant - Query/Query analysis/Predictive
                                         # Signals, no admin/infra pages; can_trade defaults off (research role,
                                         # not an execution one) but an admin can still grant it explicitly


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
    # Which symbols this connector feeds, as a JSON list e.g. '["AAPL","MSFT"]'.
    # "[]" (the default) means "the connector's full built-in universe" - unchanged
    # behaviour from before this field existed. A non-empty list scopes the feed to
    # just that symbol group (see routers/connectors.py for how it's applied live).
    symbols_json: str = "[]"
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


class QueryCostEvent(SQLModel, table=True):
    """One row per query-workspace execution, for per-tenant query cost
    governance (app/query_cost.py) - budgets, throttling, showback.

    Deliberately a SEPARATE, persisted table from query_profile.py's
    in-memory ring buffer: that buffer is "what's been slow recently" for a
    support engineer (200 entries, resets on restart, by design - see its
    own docstring); this is billing/governance data, which has to survive a
    restart to mean anything as a "budget". Kept minimal (no query text) -
    query_profile already keeps recent query text for debugging, this table
    exists purely to sum cost per tenant over a time window."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    actor: str
    elapsed_ms: float
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)


class MetricsSnapshot(SQLModel, table=True):
    """One row per periodic capture (app/metrics_history.py) of the SAME
    numbers /metrics/snapshot already exposes live - container counts, row
    counts, shard health - so the Metrics page can show a trend instead of
    only ever "right now" (refresh the page, lose the last hour). Global,
    not tenant-scoped: like /metrics/snapshot itself (no auth/tenant
    filtering today), this reflects the whole local data-plane instance,
    not per-tenant data - see topology.py's own docstring on the
    single-tenant-dedicated vs multi-tenant-hosted distinction this
    codebase draws elsewhere. Retention-purged (metrics_history.py), not
    kept forever - a rolling trend window, not an audit trail."""
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)
    containers_running: int = 0
    containers_total: int = 0
    rows_trade: int = 0
    rows_risk: int = 0
    shards_healthy: int = 0
    shards_total: int = 0


# --------------------------------------------------------------------------- infra profiles
class InfraProfile(SQLModel, table=True):
    """A reusable, named bundle of NON-SECRET infrastructure coordinates
    (region, VPC/subnet, namespace, storage class, ...) for one provider -
    exactly the fields app.tickhouse.config_fields(provider) already collects
    per TickHouse, just saved once under a name so an admin doesn't retype
    them for every new cluster.

    Tenant-scoped, same as TickHouse/Agent - each tenant (bank) deploys into
    ITS OWN cloud accounts via its own fleet agents, so "AWS Production"
    means a different VPC/account per tenant; a single global profile pool
    wouldn't line up with that BYOC model. Managed by that tenant's own
    tenant_admin ("Admin" in the UI) via require_admin, not platform_admin -
    see routers/infra_profiles.py.

    Deliberately holds no credentials at all - this platform's fleet agent
    reaches its cloud/cluster with AMBIENT identity (its node's IAM role, its
    pod's kube service account; see fleet_agent/backends.py), never
    credentials the control plane hands it. Storing access keys/service-
    principal secrets/SSH keys here would reverse that design, so this table
    doesn't have a column for any of them.

    Picking a profile at TickHouse-creation time COPIES its config_json into
    that TickHouse's own target_config once - it's not a live reference -
    so editing or deleting a profile later never silently changes a TickHouse
    that already used it.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    name: str = Field(index=True)
    description: str = ""
    provider: str                                              # aws / azure / gcp / onprem - see tickhouse.CLOUDS
    config_json: str = "{}"                                    # dict matching tickhouse.config_fields(provider)
    is_default: bool = False                                   # at most one default per (tenant, provider) - enforced in router
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: str = ""


# --------------------------------------------------------------------------- cloud auto-provisioning
class CloudProvisionRun(SQLModel, table=True):
    """One "give us your cloud credentials, we'll build the cluster"
    request: `app/cloud_provisioner.py` runs `terraform apply` against
    `terraform/{aws,azure,gcp}/` with these credentials, then `helm
    install`s this platform onto the cluster it just created, then
    provisions a TickHouse against it - the credentials-only counterpart to
    the option-based wizard (routers/tickhouse.py), which assumes a cluster
    (and an agent already enrolled into it) already exist.

    This is DELIBERATELY a different trust model than InfraProfile (see its
    own docstring: "holds no credentials at all... fleet agent reaches its
    cloud/cluster with AMBIENT identity"). InfraProfile's principle is about
    ONGOING management of infrastructure that already exists; this table is
    about the one-time (or per-update) act of CREATING that infrastructure
    in the first place, which fundamentally cannot be done with ambient
    identity - there's no cluster yet for a pod/agent to have a role in.
    Once terraform apply succeeds and an agent is enrolled into the new
    cluster, all ONGOING provisioning against it goes back through the
    normal agent/ambient-identity path - these credentials are not kept
    around for that; see credentials_encrypted's own note below.

    credentials_encrypted is the ONLY place raw cloud credentials are ever
    persisted anywhere in this codebase - Fernet-encrypted (app/
    cloud_credentials.py) with a key from CLOUD_CREDENTIALS_ENCRYPTION_KEY,
    never logged, never returned by any API response (see routers/
    cloud_provision.py's response models, which never include this field).
    Decrypted only in-process, just-in-time, for the duration of a single
    terraform/helm invocation, passed via subprocess environment variables
    (or a 0600 tempfile terraform itself requires, e.g. GCP's service
    account JSON) - never written into a tfvars file, never appears in a
    logged command line. confirm_ack must exactly equal
    cloud_provisioner.CONFIRM_PHRASE before apply() will run at all - same
    deliberate-friction pattern as alpaca_broker.LIVE_ACK_PHRASE, since this
    creates real, billed cloud resources and there is no safe default to
    silently downgrade to the way live trading has "paper" to fall back to.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    name: str = Field(index=True)                              # cluster/deployment name, e.g. "acme-prod"
    provider: str                                              # aws / azure / gcp
    region: str                                                # AWS/GCP region or Azure location
    cluster_profile: str = "ha"                                # ha / performance / cost_optimized - see terraform module's own var
    project_id: str = ""                                       # gcp only
    subscription_id: str = ""                                  # azure only
    credentials_encrypted: str = ""                            # Fernet ciphertext - see docstring above; NEVER returned by any API response
    status: str = "pending"                                    # pending/planning/applying/installing/provisioning_tickhouse/complete/failed
    status_detail: str = ""                                    # last human-readable status line
    log_tail: str = ""                                         # last ~200 lines of terraform/helm output, credential-scrubbed
    terraform_outputs_json: str = "{}"                         # non-secret outputs (cluster endpoint, etc.) once apply succeeds
    tickhouse_id: Optional[int] = Field(default=None, foreign_key="tickhouse.id")
    error_detail: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str = ""


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


class DailyPnlBaseline(SQLModel, table=True):
    """One row per (tenant, trading_date) - the tenant's total realized P&L
    (summed across every Position row) at the FIRST pretrade check of that
    day, captured lazily rather than by a scheduler (nothing to compute
    until a check actually needs it). app/risk_check.py's
    check_portfolio_limits reads "today's loss so far" as current total
    minus this baseline, for the opt-in daily-loss limit."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    trading_date: date = Field(index=True)
    baseline_realized_pnl: float = 0.0


# --------------------------------------------------------------------------- asset universe
class AssetMetadata(SQLModel, table=True):
    """One row per symbol ever seen trading anywhere on this platform -
    the canonical classification (asset class, venue, currency) app/
    asset_metadata.py derives once and app/symbol_discovery.py's poll loop
    keeps current. Global, not tenant-scoped: like symbols.py's own
    in-memory reference list this replaces the persistence for, a symbol's
    identity (AAPL is an equity, BTC-USD is crypto) doesn't vary by tenant -
    only which of a tenant's TickHouses actually carries it does.

    Previously this classification lived ONLY in an in-memory dict
    (symbols.py's _live_symbols) that started empty on every container
    restart - a live feed's ~1000+ discovered symbols got silently
    rediscovered from scratch after every redeploy, and there was no
    asset-class distinction at all (crypto pairs were tagged "CRYPTO", but
    FX pairs and every equity/live symbol both fell into an undifferentiated
    "LIVE" bucket). This table persists what's already been classified and
    adds the missing asset_class dimension, so the Markets page (and any
    future asset-class-aware feature - risk limits, alerting - per the
    Autoverse direction) has one place to ask "what kind of thing is this
    symbol" instead of re-deriving it from the symbol string each time."""
    symbol: str = Field(primary_key=True)
    name: str = ""
    asset_class: str = "unknown"   # equity / crypto / fx / commodity / unknown
    market: str = ""               # exchange/venue label, e.g. NASDAQ, LSE, CRYPTO, LIVE
    currency: str = ""
    source: str = "live"           # seed / live - matches symbols.py's own distinction
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow, index=True)


# --------------------------------------------------------------------------- signal bot
class BotConfig(SQLModel, table=True):
    """One tenant's server-side trade signal engine config (app/signal_engine.py,
    app/bot_scheduler.py). The promoted, persisted successor to
    web-ui/src/pages/Bot.jsx's config, which lived only in that browser's
    localStorage - moving it here is what lets the bot itself move server-side:
    a background scheduler reads `enabled` tenants on an interval regardless of
    whether anyone has the page open. One row per tenant (unique tenant_id).

    risk_pct/max_positions/symbols are all re-clamped server-side on write
    (routers/bot.py) to the same hard caps Bot.jsx enforced client-side
    (MAX_RISK_PCT/MAX_BASKET/MAX_POSITIONS_CAP in app/signal_engine.py) - that
    used to be a client-only cap, meaning a direct API call could bypass it;
    persisting and re-validating server-side closes that gap for real."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True, unique=True)
    enabled: bool = False
    mode: str = "manual"                      # "manual" (curated basket) / "auto" (screens the live universe)
    symbols_json: str = "[]"                  # manual mode basket, JSON list e.g. '["AAPL","MSFT"]'
    max_positions: int = 3                    # auto mode - concurrent-position cap
    paper_capital: float = 10000.0
    risk_pct: float = 1.0                     # % of paper_capital risked in aggregate across open positions
    stop_loss_pct: float = 1.5
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: str = ""


class BotPosition(SQLModel, table=True):
    """One open position the signal bot itself opened and is actively
    watching for its exit (stop-loss or trend flip) - separate from Position
    (a tenant's overall net position, which this still folds into via the
    same order-fill path) because the bot needs to remember its OWN entry
    price and stop level per symbol, not just a weighted-average cost."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    symbol: str = Field(index=True)
    qty: float
    entry_price: float
    stop_price: float
    order_id: Optional[int] = Field(default=None, foreign_key="order.id")
    opened_at: datetime = Field(default_factory=datetime.utcnow)


class BotLogEntry(SQLModel, table=True):
    """One decision (or non-decision) the signal bot made, for the activity
    feed - the persisted successor to Bot.jsx's in-memory `log` state, which
    reset on every tab close/reload."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)
    symbol: Optional[str] = None
    type: str                                 # open / hold / skip / close-win / close-loss / error
    reason: str = ""


# --------------------------------------------------------------------------- TradingView webhook
class TradingViewWebhook(SQLModel, table=True):
    """One tenant's inbound TradingView alert-webhook config (routers/
    tradingview_webhook.py). One row per tenant (unique tenant_id) - same
    "singleton config" shape as BotConfig, for the same reason: this is
    another automated, unattended order-placing surface, so it gets the same
    explicit-enable/hard-cap treatment as the signal bot, not a looser one
    just because the trigger is external.

    `token` is the ENTIRE auth mechanism - TradingView's alert webhooks
    cannot send custom headers or a signed body on non-Enterprise plans, so
    the shared secret has to live in the URL itself
    (/webhooks/tradingview/{token}). Treat it as a bearer credential: anyone
    who has it can place orders (within allowed_symbols/max_qty) against
    this tenant. secrets.token_urlsafe(32) at creation, rotatable on demand
    (routers/tradingview_webhook.py's rotate endpoint) - there is no way to
    additionally verify the CALLER is really TradingView, only that they
    knew the token.

    allowed_symbols_json is a hard allowlist, not a suggestion: enabling
    requires at least one symbol configured (mirrors BotConfig's own
    "add a symbol before enabling" guard) specifically so a leaked/guessed
    token can't be used to trade an arbitrary symbol the tenant never
    intended to wire up. max_qty is a hard per-order cap re-clamped
    server-side (routers/tradingview_webhook.py), same "don't trust what's
    posted" posture as BotConfig.risk_pct."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True, unique=True)
    enabled: bool = False
    token: str = Field(index=True, unique=True)
    allowed_symbols_json: str = "[]"          # JSON list, e.g. '["AAPL","MSFT"]' - hard allowlist
    max_qty: float = 1.0
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: str = ""
    last_triggered_at: Optional[datetime] = None


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


# --------------------------------------------------------------------------- feed handler (data-plane/feedhandler-cpp)
class FeedHandlerInstance(SQLModel, table=True):
    """One tenant's activated market-data feed - the admin-portal-managed
    counterpart to data-plane/feedhandler-cpp's C++ engine (see that
    directory's README for the protocol/venue adapter platform this
    configures). A row here is "NASDAQ TotalView-ITCH, my prod config, my
    credentials" - the engine itself reads the shape this produces via
    GET /feedhandlers/{id}/config (config_json merged with the DECRYPTED
    secrets_json) rather than the control plane pushing config to it, the
    same pull-based shape the fleet agent already uses elsewhere in this
    codebase.

    config_json holds ONLY non-secret coordinates (transport/protocol/venue
    adapter selection, host/port/multicast group, field mappings) - the
    exact shape data-plane/feedhandler-cpp/config/providers/*.json ships as
    illustrative defaults. secrets_json is Fernet-encrypted at rest (see
    app/crypto.py, the same encryption LLMConfig/TenantIdP/TenantLDAP
    already use this session) and only ever decrypted in memory when the
    engine's own config endpoint is called - never returned by any list/get
    response otherwise.

    tickhouse_id is nullable and deliberately the FK direction (a feed
    handler points at ONE TickHouse, not the reverse) - one TickHouse's
    tickerplants can legitimately ingest from several feed handlers at once
    (e.g. NASDAQ ITCH and a crypto WebSocket feed both landing in the same
    cluster), so TickHouse doesn't hold a single feed_handler_id back.
    Nullable because a feed handler can be activated stand-alone (see
    KdbPublisherConfig's own env-var-driven default target) before ever
    being tied to a specific declared TickHouse - association is optional,
    not required to activate a source."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    tickhouse_id: Optional[int] = Field(default=None, foreign_key="tickhouse.id", index=True)
    provider: str = Field(index=True)            # catalog key, e.g. "NASDAQ", "COINBASE" - see feedhandler_catalog.py
    feed: str = ""                                # e.g. "TOTALVIEW_ITCH", "MATCHES"
    display_name: str = ""
    enabled: bool = False
    config_json: str = "{}"                       # FeedConfig-shaped JSON, non-secret (see class docstring)
    secrets_json: str = ""                        # Fernet-encrypted credential values, "" if the provider needs none
    status: str = "configured"                    # configured | validating | live | degraded | error - operator-set/last-known, not live-polled (see routers/feedhandlers.py)
    last_error: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: str = ""
