from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import User
from ..security import create_access_token, decode_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
bearer_scheme = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    tenant_id: Optional[int]


class CurrentUser(BaseModel):
    user_id: int
    email: str
    role: str
    tenant_id: Optional[int]


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.email == body.email)).first()
    if user is None or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    token = create_access_token(user.id, user.email, user.role.value, user.tenant_id)
    return TokenResponse(access_token=token, role=user.role.value, tenant_id=user.tenant_id)


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> CurrentUser:
    if creds is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        payload = decode_access_token(creds.credentials)
    except Exception:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return CurrentUser(
        user_id=int(payload["sub"]), email=payload["email"],
        role=payload["role"], tenant_id=payload.get("tenant_id"),
    )


def require_auth(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Any authenticated user - platform admin or tenant admin."""
    return user


def require_tenant_scope(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """
    Any authenticated user, but guarantees `.tenant_id` is usable for
    scoping a query - a platform admin calling a tenant-scoped endpoint
    without an explicit tenant context is almost certainly a mistake
    (they should use the /tenants endpoints instead), so this rejects that
    case rather than silently returning null-tenant data.
    """
    if user.tenant_id is None:
        raise HTTPException(
            status_code=400,
            detail="this endpoint is tenant-scoped - platform admins should "
                   "use the /tenants management endpoints instead",
        )
    return user


def require_platform_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role != "platform_admin":
        raise HTTPException(status_code=403, detail="platform admin only")
    return user


def require_admin(user: CurrentUser = Depends(require_tenant_scope)) -> CurrentUser:
    """tenant_admin ("Admin" in the UI, full access within their own tenant)
    - platform_admin never has a tenant_id, so require_tenant_scope already
      rejects it here, which is correct: creating a TickHouse/infra profile/
      user is that tenant's own admin's job, not the SaaS operator's (who
      manages tenants themselves via /tenants, not their infrastructure).
    Gates infra-provisioning and user-management actions (TickHouse/Fleet
    create-delete-provision, connector/subscriber config, infra profiles,
    user management) so a functional_user/quant_analyst can operate
    day-to-day without also being able to reshape the tenant's
    infrastructure or other people's accounts."""
    if user.role != "tenant_admin":
        raise HTTPException(status_code=403, detail="admin access required")
    return user
