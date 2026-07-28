from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlmodel import Session, select

from ..config import settings
from ..db import get_session, log_event
from ..models import AuditEvent
from .auth import CurrentUser, require_auth

router = APIRouter(prefix="/audit", tags=["audit"])


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
        q = q.where(AuditEvent.tenant_id == user.tenant_id)
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
