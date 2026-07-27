import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session, log_event
from ..models import Tenant, User, UserRole
from ..security import hash_password
from .auth import CurrentUser, require_platform_admin

router = APIRouter(prefix="/tenants", tags=["tenants"])


class CreateTenantRequest(BaseModel):
    name: str
    admin_email: str
    admin_password: str
    plan: str = "trial"


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


@router.get("")
def list_tenants(admin: CurrentUser = Depends(require_platform_admin),
                  session: Session = Depends(get_session)):
    tenants = session.exec(select(Tenant)).all()
    return [t.model_dump() for t in tenants]


@router.post("")
def create_tenant(body: CreateTenantRequest, admin: CurrentUser = Depends(require_platform_admin),
                   session: Session = Depends(get_session)):
    slug = _slugify(body.name)
    if session.exec(select(Tenant).where(Tenant.slug == slug)).first():
        raise HTTPException(status_code=409, detail=f"a tenant with slug '{slug}' already exists")
    if session.exec(select(User).where(User.email == body.admin_email)).first():
        raise HTTPException(status_code=409, detail="a user with that email already exists")

    tenant = Tenant(name=body.name, slug=slug, plan=body.plan)
    session.add(tenant)
    session.flush()

    user = User(
        tenant_id=tenant.id, email=body.admin_email,
        password_hash=hash_password(body.admin_password),
        role=UserRole.tenant_admin,
    )
    session.add(user)
    session.commit()
    session.refresh(tenant)
    result = tenant.model_dump()

    log_event(session, admin.email, "create_tenant", tenant.slug,
              detail=f"admin={body.admin_email}")
    return result


@router.post("/{tenant_id}/suspend")
def suspend_tenant(tenant_id: int, admin: CurrentUser = Depends(require_platform_admin),
                    session: Session = Depends(get_session)):
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    tenant.status = "suspended"
    session.add(tenant)
    session.commit()
    log_event(session, admin.email, "suspend_tenant", tenant.slug)
    return {"tenant_id": tenant_id, "status": "suspended"}
