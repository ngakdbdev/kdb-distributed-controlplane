import logging
from urllib.parse import urlparse

from sqlmodel import Session, SQLModel, create_engine, select

from .config import settings
from .models import (AuditEvent, Connector, Subscriber, Tenant, TenantIdP,
                     TenantLDAP, User, UserRole)
from .security import hash_password

log = logging.getLogger("db")


def build_engine(database_url: str):
    """
    Builds a SQLAlchemy engine appropriate to whichever database dialect the
    operator pointed DATABASE_URL at. SQLite gets the local-file settings
    that make it usable for a demo; every real server database (Postgres,
    MySQL, SQL Server) gets a connection pool with pre-ping and recycling,
    because unlike SQLite, a real DB connection can silently go stale
    (managed DB failover, idle timeout, load balancer reset) and pool
    pre-ping is what catches that before a request fails on it.
    """
    dialect = urlparse(database_url).scheme.split("+")[0]

    if dialect == "sqlite":
        log.warning(
            "DATABASE_URL is sqlite - fine for a local demo, NOT recommended "
            "for a customer-facing SaaS deployment (no HA, no concurrent-writer "
            "safety across replicas). Point at postgres/mysql/mssql for anything real."
        )
        return create_engine(database_url, echo=False, connect_args={"check_same_thread": False})

    if dialect not in ("postgresql", "mysql", "mssql"):
        raise ValueError(
            f"unsupported database dialect '{dialect}' in DATABASE_URL - "
            "expected sqlite, postgresql+psycopg2, mysql+pymysql, or mssql+pyodbc"
        )

    log.info("connecting to %s database (pool_size=%d)", dialect, settings.db_pool_size)
    return create_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
    )


engine = build_engine(settings.database_url)


def init_db():
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_platform_admin(session)
        if settings.seed_demo_tenant:
            _seed_demo_tenant(session)
        session.commit()


def _seed_platform_admin(session: Session):
    existing = session.exec(
        select(User).where(User.role == UserRole.platform_admin)
    ).first()
    if existing:
        return
    session.add(User(
        tenant_id=None,
        email=settings.platform_admin_email,
        password_hash=settings.platform_admin_password_hash,
        role=UserRole.platform_admin,
    ))
    log.info("seeded platform admin user: %s", settings.platform_admin_email)


def _seed_demo_tenant(session: Session):
    existing = session.exec(select(Tenant).where(Tenant.slug == "demo-bank")).first()
    if existing:
        return

    tenant = Tenant(name="Demo Bank", slug="demo-bank", plan="trial")
    session.add(tenant)
    session.flush()  # populates tenant.id without a separate commit

    session.add(User(
        tenant_id=tenant.id,
        email=settings.demo_tenant_admin_email,
        password_hash=settings.demo_tenant_admin_password_hash,
        role=UserRole.tenant_admin,
    ))
    session.add(Connector(
        tenant_id=tenant.id, name="bpipe-sim", kind="equities",
        description="Synthetic B-PIPE-style equities trade feed",
        service_name="bpipe-sim", enabled=False,
    ))
    session.add(Connector(
        tenant_id=tenant.id, name="crims-sim", kind="risk",
        description="Synthetic CRIMS-style risk/reference feed",
        service_name="crims-sim", enabled=False,
    ))
    # Real market-data providers (data-plane/feeds/providers/) - each one is
    # ALSO a plain docker-compose service (profiles: ["providers"]) and was
    # controllable only that way until now; registering them here puts them
    # on the SAME on/off toggle the Connectors screen already gives
    # bpipe-sim/crims-sim, via the same generic service_name -> orchestrator
    # start/stop path (routers/connectors.py has no per-connector special
    # casing). Symbol-group editing (PUT .../symbols) is NOT wired for these
    # yet - their symbol list is a compose-time arg (${X_SYMBOLS:-...}), not
    # a runtime env var a running container can pick up, unlike bpipe-sim/
    # crims-sim's own BPIPE_SYMBOLS/CRIMS_SYMBOLS.
    for name, kind, description in (
        ("finnhub-feed", "equities", "Finnhub live equities feed (needs FINNHUB_API_KEY)"),
        ("twelvedata-feed", "equities", "Twelve Data live equities feed (needs TWELVEDATA_API_KEY)"),
        ("coinbase-feed", "crypto", "Coinbase live crypto trades - public feed, no key needed"),
        ("kraken-feed", "crypto", "Kraken live crypto trades - public feed, no key needed"),
        ("binance-feed", "crypto", "Binance live crypto trades - public feed, no key needed"),
        ("bybit-feed", "crypto", "Bybit live crypto trades - public feed, no key needed"),
        ("okx-feed", "crypto", "OKX live crypto trades - public feed, no key needed"),
    ):
        session.add(Connector(
            tenant_id=tenant.id, name=name, kind=kind, description=description,
            service_name=name, enabled=False,
        ))
    session.add(Subscriber(tenant_id=tenant.id, name="demo-dashboard", table="trade", role="read-only"))
    session.add(Subscriber(tenant_id=tenant.id, name="demo-dashboard", table="risk", role="read-only"))
    log.info("seeded demo tenant '%s' with admin %s", tenant.slug, settings.demo_tenant_admin_email)


def get_session():
    with Session(engine) as session:
        yield session


def log_event(session: Session, actor: str, action: str, target: str = "",
              detail: str = "", outcome: str = "success", tenant_id: int = None):
    event = AuditEvent(actor=actor, action=action, target=target,
                        detail=detail, outcome=outcome, tenant_id=tenant_id)
    session.add(event)
    session.commit()
    return event
