from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session, log_event
from ..models import Connector
from ..orchestrator import orchestrator
from .auth import CurrentUser, require_tenant_scope

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.get("")
def list_connectors(user: CurrentUser = Depends(require_tenant_scope),
                     session: Session = Depends(get_session)):
    connectors = session.exec(select(Connector).where(Connector.tenant_id == user.tenant_id)).all()
    return [
        {**c.model_dump(), "live_status": orchestrator.status(c.service_name)}
        for c in connectors
    ]


@router.post("/{connector_id}/toggle")
def toggle_connector(connector_id: int, user: CurrentUser = Depends(require_tenant_scope),
                      session: Session = Depends(get_session)):
    connector = session.get(Connector, connector_id)
    if connector is None or connector.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="connector not found")

    if connector.enabled:
        ok = orchestrator.stop(connector.service_name)
        action = "disable_connector"
        if ok:
            connector.enabled = False
    else:
        ok = orchestrator.start(connector.service_name)
        action = "enable_connector"
        if ok:
            connector.enabled = True

    session.add(connector)
    session.commit()
    session.refresh(connector)
    result = connector.model_dump()  # snapshot before log_event's commit expires it

    log_event(session, user.email, action, connector.name,
              outcome="success" if ok else "failure", tenant_id=user.tenant_id)
    return result
