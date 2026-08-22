"""
platform_health.py (router) - the single "is everything healthy" rollup the
Overview page's health card reads, instead of an operator having to check
Topology, Metrics, and Audit log separately to answer that one question.

Composes EXISTING data sources rather than standing up a new observability
subsystem: orchestrator.status_all() (already backs the Topology page),
gateway_client.health() (already backs the Metrics page), and AuditEvent
(already backs the Audit log page). No new metrics pipeline, no new
storage - just one aggregation endpoint over what's already collected.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from .. import oms
from ..db import get_session
from ..kdb_client import gateway_client
from ..models import AuditEvent
from ..orchestrator import orchestrator
from .auth import CurrentUser, require_tenant_scope
from . import trading as trading_router

router = APIRouter(prefix="/platform", tags=["platform"])

_STATUS_RANK = {"healthy": 0, "unknown": 1, "live": 2, "degraded": 2, "critical": 3}


def _worst(*statuses: str) -> str:
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 1))


def _infrastructure_component() -> dict:
    services = orchestrator.status_all()
    total = len(services)
    running = sum(1 for s in services.values() if str(s).lower() in ("running", "up"))
    down = [name for name, s in services.items() if str(s).lower() not in ("running", "up")]
    if total == 0:
        status = "unknown"
    elif running == total:
        status = "healthy"
    elif running >= total * 0.7:
        status = "degraded"
    else:
        status = "critical"
    detail = f"{running}/{total} containers running"
    if down:
        detail += f" - down: {', '.join(down[:5])}" + (", ..." if len(down) > 5 else "")
    return {"status": status, "detail": detail}


def _tickhouse_component() -> dict:
    """.gw.health[] is a list of per-shard dicts, each with its OWN nested
    tp/rdb/idb/wdb/hdb sub-status (no top-level "status" field on the shard
    itself) - confirmed live: an earlier version of this check read
    h.get("status") on the shard dict directly, which is always None, so
    every shard silently counted as down regardless of real health. rdb
    (the live query path) and hdb (historical) are deliberately scored
    separately: a shard with rdb up but hdb down still serves live queries
    fine, so that's "degraded", not "critical" - conflating the two would
    cry wolf over a real day-to-day (hdb reload lag, EOD backlog) that
    doesn't affect anyone querying live data right now."""
    try:
        health = gateway_client.health()
    except Exception as exc:  # noqa: BLE001
        return {"status": "unknown", "detail": f"gateway unreachable: {exc}"}
    if not health:
        return {"status": "unknown", "detail": "no shards reported"}
    total = len(health)
    rdb_up = sum(1 for h in health if str((h.get("rdb") or {}).get("status", "")).lower() == "up")
    hdb_down = [h.get("shard") for h in health
               if str((h.get("hdb") or {}).get("status", "")).lower() != "up"]
    if rdb_up < total:
        return {"status": "critical", "detail": f"only {rdb_up}/{total} shard(s) have a live rdb"}
    if hdb_down:
        return {"status": "degraded",
                "detail": f"live data flowing on all {total} shard(s); hdb down on: {', '.join(hdb_down)}"}
    return {"status": "healthy", "detail": f"{total}/{total} shard(s) fully healthy (rdb + hdb)"}


def _security_component(session: Session, tenant_id: int) -> dict:
    since = datetime.utcnow() - timedelta(minutes=15)
    failures = session.exec(
        select(func.count()).select_from(AuditEvent)
        .where(AuditEvent.tenant_id == tenant_id, AuditEvent.outcome == "failure",
               AuditEvent.timestamp >= since)
    ).one()
    if failures == 0:
        status = "healthy"
    elif failures < 5:
        status = "degraded"
    else:
        status = "critical"
    return {"status": status, "detail": f"{failures} failed action(s) in the last 15 minutes"}


def _trading_component() -> dict:
    # Reuses trading.py's own _router() rather than re-deriving broker/mode
    # logic here - two places computing "which broker is active" could
    # silently drift out of sync with each other.
    #
    # "live" is its own status, deliberately NOT "critical" - a correctly
    # configured live-trading route is a real, intentional operator choice,
    # not a fault. Conflating "real money can move" with "something is
    # broken" would either cry wolf every time live trading is correctly
    # on, or (worse) train an operator to stop trusting "critical" as a
    # real signal. Only misconfiguration (both brokers configured at once)
    # is actually critical here.
    try:
        r = trading_router._router()
    except oms.OrderError as exc:
        return {"status": "critical", "detail": f"misconfigured: {exc}"}
    live = r.route_name in ("alpaca-live", "ibkr-live")
    return {"status": "live" if live else "healthy",
            "detail": f"routing via {r.route_name}" + (" - REAL MONEY" if live else "")}


@router.get("/health")
def platform_health(user: CurrentUser = Depends(require_tenant_scope),
                    session: Session = Depends(get_session)):
    """One rollup: infrastructure (containers), tickhouse (gateway/shards),
    security (recent failed actions), trading (which broker/mode is live).
    `trading`'s status is "critical" only in the sense of "pay attention,
    real money can move" - not necessarily unhealthy."""
    components = {
        "infrastructure": _infrastructure_component(),
        "tickhouse": _tickhouse_component(),
        "security": _security_component(session, user.tenant_id),
        "trading": _trading_component(),
    }
    overall = _worst(*(c["status"] for c in components.values()))
    return {"overall": overall, "components": components,
            "checked_at": datetime.utcnow().isoformat()}
