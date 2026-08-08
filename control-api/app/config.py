import os
from dataclasses import dataclass, field

from . import topology


@dataclass
class Settings:
    # Platform admin: you, the SaaS operator. Created on first boot if no
    # platform_admin user exists yet. Tenant admins are created per-tenant
    # via the platform admin's /tenants API instead of a static config value.
    platform_admin_email: str = os.environ.get("PLATFORM_ADMIN_EMAIL", "admin@platform.local")
    # `or default` rather than `.get(key, default)`: docker-compose's
    # "${VAR:-}" substitution always sets the env var, just to "" when VAR is
    # unset on the host - .get()'s default only kicks in when the key is
    # ABSENT, so an unset host var silently became an empty password hash
    # (every login rejected, with no error indicating why) instead of falling
    # back to the documented "changeme" default. `or` treats "" as unset too.
    platform_admin_password_hash: str = os.environ.get("PLATFORM_ADMIN_PASSWORD_HASH") or (
        # bcrypt hash of "changeme" - CHANGE THIS before any real deployment.
        # Generate your own with:
        #   python -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"
        "$2b$12$FmUqCIyDLBHXtlKgNKuPF.ewEwICY7yNgfQ38/L.4NIgjWK5LMcUG"
    )

    # A demo tenant + tenant-admin user, seeded on first boot purely so the
    # UI has something to log into out of the box. Real tenants are created
    # via POST /tenants (platform admin only) - this is not how you'd
    # onboard an actual bank.
    seed_demo_tenant: bool = os.environ.get("SEED_DEMO_TENANT", "true").lower() == "true"
    demo_tenant_admin_email: str = os.environ.get("DEMO_TENANT_ADMIN_EMAIL", "admin@demo-bank.local")
    demo_tenant_admin_password_hash: str = os.environ.get("DEMO_TENANT_ADMIN_PASSWORD_HASH") or (
        "$2b$12$FmUqCIyDLBHXtlKgNKuPF.ewEwICY7yNgfQ38/L.4NIgjWK5LMcUG"  # also "changeme"
    )

    jwt_secret: str = os.environ.get("JWT_SECRET", "dev-secret-change-in-deploy")
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60 * 8

    watchdog_shared_secret: str = os.environ.get("WATCHDOG_SHARED_SECRET", "dev-watchdog-secret-change-in-deploy")

    database_url: str = os.environ.get("DATABASE_URL", "sqlite:///./control_plane.db")
    # Supported dialects and the driver each one needs (already pinned in
    # requirements.txt):
    #   sqlite      sqlite:///./control_plane.db                     (default, local/demo only)
    #   postgres    postgresql+psycopg2://user:pass@host:5432/dbname (psycopg2-binary)
    #   mysql       mysql+pymysql://user:pass@host:3306/dbname       (pymysql)
    #   sql server  mssql+pyodbc://user:pass@host:1433/dbname?driver=ODBC+Driver+18+for+SQL+Server
    #               (pyodbc + msodbcsql18 - see control-api/README-database.md for the driver install)
    # A bank customer typically already runs one of postgres/mysql/mssql
    # under their own change-management process, so pointing the control
    # plane at their existing managed database (RDS/Cloud SQL/Azure
    # Database/Azure SQL) is usually preferred over standing up a new one.
    db_pool_size: int = int(os.environ.get("DB_POOL_SIZE", "5"))
    db_pool_max_overflow: int = int(os.environ.get("DB_POOL_MAX_OVERFLOW", "10"))
    db_pool_recycle_seconds: int = int(os.environ.get("DB_POOL_RECYCLE_SECONDS", "1800"))

    docker_compose_project: str = os.environ.get("COMPOSE_PROJECT_NAME", "kdb-control-plane")

    gateway_host: str = os.environ.get("GATEWAY_HOST", "gateway")
    gateway_port: int = int(os.environ.get("GATEWAY_PORT", str(topology.TIER_PORTS["gateway"])))
    gateway_timeout_sec: float = float(os.environ.get("GATEWAY_TIMEOUT_SEC", "5"))

    # --- Natural-language-to-q (app/nl2q.py, app/llm_provider.py) ---
    # "none" (default) disables the LLM path entirely - the query workspace
    # falls back to its offline regex generator, so the feature works with
    # zero configuration. "anthropic" talks to the Claude API (or a private
    # Anthropic-compatible gateway via NL2Q_LLM_BASE_URL). "openai_compatible"
    # is a plain OpenAI-shaped /chat/completions call - covers OpenAI, Azure
    # OpenAI, and - the case that matters for an on-prem/air-gapped
    # TickHouse - a local model server (Ollama, vLLM, LM Studio) pointed to
    # via NL2Q_LLM_BASE_URL, so this is portable to a deployment with no
    # path to any public API.
    nl2q_llm_provider: str = os.environ.get("NL2Q_LLM_PROVIDER", "none")
    nl2q_llm_model: str = os.environ.get("NL2Q_LLM_MODEL", "claude-opus-5")
    nl2q_llm_api_key: str = os.environ.get("NL2Q_LLM_API_KEY", "")
    nl2q_llm_base_url: str = os.environ.get("NL2Q_LLM_BASE_URL", "")
    nl2q_llm_timeout_sec: float = float(os.environ.get("NL2Q_LLM_TIMEOUT_SEC", "20"))

    # --- SSO / Microsoft Entra ---
    # public_base_url is where THIS api is reachable from the user's browser
    # (used to build the OIDC redirect_uri); web_ui_url is where to send the
    # user after a successful SSO login.
    public_base_url: str = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")
    web_ui_url: str = os.environ.get("WEB_UI_URL", "http://localhost")
    sso_scopes: str = os.environ.get("SSO_SCOPES", "openid profile email")
    sso_state_ttl_sec: int = int(os.environ.get("SSO_STATE_TTL_SEC", "600"))

    # Shard count is the single scaling knob. Everything below - which q
    # processes exist, which the orchestrator/watchdog manage, how the
    # gateway routes - derives from it via app.topology. Default 2 keeps the
    # historical A-M / N-Z behaviour.
    shard_count: int = int(os.environ.get("SHARD_COUNT", "2"))

    # {container name -> service name}, derived from shard_count. Filled in
    # __post_init__ so it tracks whatever SHARD_COUNT was set to.
    managed_services: dict = field(default_factory=dict)

    def __post_init__(self):
        # validates shard_count (raises on <1 or >26) and builds the full
        # per-shard + gateway + feed-sim service map for this topology
        self.managed_services = topology.managed_services(self.shard_count)


settings = Settings()
