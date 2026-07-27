from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session, log_event
from ..models import Subscriber
from .auth import CurrentUser, require_tenant_scope

router = APIRouter(prefix="/subscribers", tags=["subscribers"])


@router.get("")
def list_subscribers(user: CurrentUser = Depends(require_tenant_scope),
                      session: Session = Depends(get_session)):
    subs = session.exec(select(Subscriber).where(Subscriber.tenant_id == user.tenant_id)).all()
    return [s.model_dump() for s in subs]


@router.post("")
def add_subscriber(sub: Subscriber, user: CurrentUser = Depends(require_tenant_scope),
                    session: Session = Depends(get_session)):
    sub.id = None
    sub.tenant_id = user.tenant_id  # never trust a tenant_id from the request body
    session.add(sub)
    session.commit()
    session.refresh(sub)
    result = sub.model_dump()  # snapshot before log_event's commit expires it
    log_event(session, user.email, "add_subscriber", f"{sub.name}:{sub.table}", tenant_id=user.tenant_id)
    return result


@router.delete("/{subscriber_id}")
def remove_subscriber(subscriber_id: int, user: CurrentUser = Depends(require_tenant_scope),
                       session: Session = Depends(get_session)):
    sub = session.get(Subscriber, subscriber_id)
    if sub is None or sub.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="subscriber not found")
    session.delete(sub)
    session.commit()
    log_event(session, user.email, "remove_subscriber", f"{sub.name}:{sub.table}", tenant_id=user.tenant_id)
    return {"removed": subscriber_id}
