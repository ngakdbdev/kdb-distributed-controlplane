"""
query_cost.py - per-tenant query cost governance for the query workspace.

Two things this codebase already had, that this ties together into an
actual product feature rather than leaving as separate pieces:
  * query_service._cap_result_rows bounds what a single query can pull
  * query_advisor._scan_risk_tip warns, per-query, before a full-table scan runs

Neither of those says anything about a tenant's AGGREGATE load over time - a
tenant could run a thousand individually-cheap queries and still starve
everyone else's shard. This adds that layer: every query execution's real
elapsed_ms is persisted (QueryCostEvent), summed per tenant over a rolling
window, and checked against a budget before the NEXT query is allowed to
run. Disabled by default (Settings.query_budget_ms_per_window == 0) - like
every other governance knob added this round, this is opt-in, not a
surprise default.

Fails OPEN on its own errors (a DB hiccup checking the budget blocks a
query workspace that would otherwise work) - this is a cost/fairness
control, not a safety control like risk_check.py, so the asymmetry there
doesn't apply here: an unavailable budget check should degrade to "allow",
not "block everyone from querying because accounting is down".
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, func, select

from .config import settings
from .models import QueryCostEvent

log = logging.getLogger("query_cost")


def record(session: Session, tenant_id: int, actor: str, elapsed_ms: float) -> None:
    session.add(QueryCostEvent(tenant_id=tenant_id, actor=actor, elapsed_ms=max(0.0, elapsed_ms)))
    session.commit()


def consumed_ms(session: Session, tenant_id: int, window_hours: Optional[float] = None) -> float:
    """Total elapsed_ms this tenant's queries have cost in the trailing
    `window_hours` (defaults to Settings.query_budget_window_hours)."""
    if window_hours is None:
        window_hours = settings.query_budget_window_hours
    since = datetime.utcnow() - timedelta(hours=window_hours)
    total = session.exec(
        select(func.sum(QueryCostEvent.elapsed_ms))
        .where(QueryCostEvent.tenant_id == tenant_id, QueryCostEvent.timestamp >= since)
    ).first()
    return float(total or 0.0)


def check_budget(session: Session, tenant_id: int) -> Optional[str]:
    """None if the tenant may run another query; otherwise a human-readable
    block reason. No-ops (always None) when no budget is configured."""
    budget = settings.query_budget_ms_per_window
    if budget <= 0:
        return None
    try:
        used = consumed_ms(session, tenant_id)
    except Exception as exc:  # noqa: BLE001 - fail open, see module docstring
        log.warning("query budget check failed for tenant %s, allowing: %s", tenant_id, exc)
        return None
    if used < budget:
        return None
    window = settings.query_budget_window_hours
    window_label = f"{window:g}h"
    return (
        f"query budget exceeded: {used / 1000:.1f}s of {budget / 1000:.1f}s used in the "
        f"trailing {window_label} - wait for older queries to roll out of the window, or "
        f"ask an admin to raise QUERY_BUDGET_MS_PER_WINDOW"
    )


def tenant_summary(session: Session, tenant_id: int) -> dict:
    """Showback: this tenant's current window consumption vs budget."""
    budget = settings.query_budget_ms_per_window
    used = consumed_ms(session, tenant_id)
    return {
        "tenant_id": tenant_id,
        "window_hours": settings.query_budget_window_hours,
        "budget_ms": budget or None,
        "consumed_ms": round(used, 1),
        "remaining_ms": round(max(0.0, budget - used), 1) if budget > 0 else None,
        "percent_used": round(100 * used / budget, 1) if budget > 0 else None,
        "enforced": budget > 0,
    }


def all_tenants_summary(session: Session) -> list[dict]:
    """Showback across every tenant with activity in the current window -
    platform-admin view. Tenants with zero queries in the window just don't
    appear (nothing to show), rather than listing every tenant at 0."""
    window_hours = settings.query_budget_window_hours
    since = datetime.utcnow() - timedelta(hours=window_hours)
    rows = session.exec(
        select(QueryCostEvent.tenant_id, func.sum(QueryCostEvent.elapsed_ms), func.count())
        .where(QueryCostEvent.timestamp >= since)
        .group_by(QueryCostEvent.tenant_id)
    ).all()
    budget = settings.query_budget_ms_per_window
    out = []
    for tenant_id, used, n in rows:
        used = float(used or 0.0)
        out.append({
            "tenant_id": tenant_id,
            "query_count": n,
            "consumed_ms": round(used, 1),
            "budget_ms": budget or None,
            "percent_used": round(100 * used / budget, 1) if budget > 0 else None,
        })
    out.sort(key=lambda r: r["consumed_ms"], reverse=True)
    return out
