import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # Platform admin: you, the SaaS operator. Created on first boot if no
    # platform_admin user exists yet. Tenant admins are created per-tenant
    # via the platform admin's /tenants API instead of a static config value.
    platform_admin_email: str = os.environ.get("PLATFORM_ADMIN_EMAIL", "admin@platform.local")
    platform_admin_password_hash: str = os.environ.get(
        "PLATFORM_ADMIN_PASSWORD_HASH",
        # bcrypt hash of "changeme" - CHANGE THIS before any real deployment.
        # Generate your own with:
        #   python -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"
        "$2b$12$FmUqCIyDLBHXtlKgNKuPF.ewEwICY7yNgfQ38/L.4NIgjWK5LMcUG",
    )

    # A demo tenant + tenant-admin user, seeded on first boot purely so the
    # UI has something to log into out of the box. Real tenants are created
    # via POST /tenants (platform admin only) - this is not how you'd
    # onboard an actual bank.
    seed_demo_tenant: bool = os.environ.get("SEED_DEMO_TENANT", "true").lower() == "true"
    demo_tenant_admin_email: str = os.environ.get("DEMO_TENANT_ADMIN_EMAIL", "admin@demo-bank.local")
    demo_tenant_admin_password_hash: str = os.environ.get(
        "DEMO_TENANT_ADMIN_PASSWORD_HASH",
        "$2b$12$FmUqCIyDLBHXtlKgNKuPF.ewEwICY7yNgfQ38/L.4NIgjWK5LMcUG",  # also "changeme"
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
    gateway_port: int = int(os.environ.get("GATEWAY_PORT", "5050"))

    # container name -> compose service name, used by the orchestrator and watchdog
    managed_services: dict = field(default_factory=lambda: {
        "tp-a-m": "tp-a-m",
        "tp-n-z": "tp-n-z",
        "wdb-a-m": "wdb-a-m",
        "wdb-n-z": "wdb-n-z",
        "rdb-a-m": "rdb-a-m",
        "rdb-n-z": "rdb-n-z",
        "idb-a-m": "idb-a-m",
        "idb-n-z": "idb-n-z",
        "gateway": "gateway",
        "bpipe-sim": "bpipe-sim",
        "crims-sim": "crims-sim",
    })


settings = Settings()
