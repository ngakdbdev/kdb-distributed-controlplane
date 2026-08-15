from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlmodel import Session, and_, or_, select

from ..config import settings
from ..db import get_session, log_event
from ..models import AuditEvent
from .auth import CurrentUser, require_auth

router = APIRouter(prefix="/audit", tags=["audit"])

# actor values used ONLY by the data plane's own self-reporting (the
# watchdog's detect_failure/auto_heal, a tickerplant's slow_sub_discard -
# both post to /audit/internal below with no tenant_id, since neither
# knows or cares which tenant's data it concerns - it's about the shared
# cluster). These are the ONLY tenant_id-IS-NULL rows a non-platform-admin
# gets to see (below) - genuinely tenant-scoped platform actions like
# create_tenant/suspend_tenant also end up with tenant_id unset (they're
# ABOUT a tenant, not scoped to the caller's own), but their actor is
# always a human admin's email, never one of these, so they stay hidden
# from anyone but platform_admin.
_PLATFORM_WIDE_ACTORS = ("watchdog",)
_PLATFORM_WIDE_ACTOR_PREFIX = "tp:"


@router.get("")
def list_audit_events(limit: int = Query(default=100, le=1000),
                       tenant_id: Optional[int] = Query(default=None, description="platform admin only"),
                       action: Optional[str] = Query(default=None, description="filter by action, e.g. slow_sub_discard"),
                       user: CurrentUser = Depends(require_auth),
                       session: Session = Depends(get_session)):
    q = select(AuditEvent).order_by(AuditEvent.timestamp.desc()).limit(limit)
    if action:
        q = q.where(AuditEvent.action == action)
    if user.role == "platform_admin":
        if tenant_id is not None:
            q = q.where(AuditEvent.tenant_id == tenant_id)
        # else: no filter - platform admin sees the full cross-tenant trail
    else:
        # Previously an exact tenant_id match, which a NULL tenant_id (the
        # watchdog's own self-healing reports, always platform-wide) could
        # never satisfy - a tenant_admin could never see the watchdog
        # actively healing THEIR OWN tick cluster. Carved out narrowly by
        # actor (see _PLATFORM_WIDE_ACTORS above), not a blanket "any NULL
        # tenant_id" - that would also leak other tenants' create_tenant/
        # suspend_tenant events, which are unrelated to this tenant.
        q = q.where(or_(
            AuditEvent.tenant_id == user.tenant_id,
            and_(AuditEvent.tenant_id.is_(None), or_(
                AuditEvent.actor.in_(_PLATFORM_WIDE_ACTORS),
                AuditEvent.actor.like(f"{_PLATFORM_WIDE_ACTOR_PREFIX}%"),
            )),
        ))
    events = session.exec(q).all()
    return [e.model_dump() for e in events]


@router.post("/internal")
def write_internal_event(event: dict, x_internal_secret: str = Header(default=""),
                          session: Session = Depends(get_session)):
    """
    Used by the watchdog and the data-plane tickerplants (and, per-tenant, by
    agents indirectly through the fleet API) to log actions without a full user
    login. The secret may be passed as the X-Internal-Secret header OR as a
    "secret" field in the body - the latter lets q processes report via a plain
    HTTP POST, which can't easily set custom headers.
    """
    supplied = x_internal_secret or event.get("secret", "")
    if supplied != settings.watchdog_shared_secret:
        raise HTTPException(status_code=401, detail="invalid internal secret")
    logged = log_event(
        session,
        actor=event.get("actor", "watchdog"),
        action=event.get("action", "unknown"),
        target=event.get("target", ""),
        detail=event.get("detail", ""),
        outcome=event.get("outcome", "success"),
        tenant_id=event.get("tenant_id"),
    )
    return logged.model_dump()
