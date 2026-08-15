"""
users.py (router) - per-tenant user management: an "Admin" (tenant_admin)
creating/editing logins for their own bank and assigning them one of this
platform's tenant-level roles (see models.UserRole):

  * tenant_admin ("Admin")     - full access within the tenant, same as the
                                  admin creating the account.
  * functional_user            - day-to-day trading/ops (Markets, Orders,
                                  Portfolio, Bot, Query); can_trade defaults
                                  on, since placing orders is the point of
                                  the role, but an admin can turn it off.
  * quant_analyst               - research/analysis (Query, Query analysis,
                                  Predictive Signals); can_trade defaults
                                  off - a research role, not an execution
                                  one - but an admin can grant it.

Did not exist before this router: previously the only way to get a second
user into a tenant was direct DB access. admin/require_admin-gated
throughout - a functional_user/quant_analyst managing OTHER accounts
(including escalating their own) would defeat the point of having roles.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session, log_event
from ..models import User, UserRole
from ..security import hash_password
from .auth import CurrentUser, require_admin

router = APIRouter(prefix="/users", tags=["users"])

# Roles an admin may assign to someone else within their own tenant.
# platform_admin is deliberately excluded - that's the SaaS operator level,
# seeded once (see db._seed_platform_admin), not something any tenant admin
# can grant.
ASSIGNABLE_ROLES = (UserRole.tenant_admin, UserRole.functional_user, UserRole.quant_analyst)

# can_trade default per role at creation time - an admin can still override
# either way; this is just what makes sense unless told otherwise.
_DEFAULT_CAN_TRADE = {
    UserRole.tenant_admin: True,       # moot - require_trading always allows admins regardless of the flag
    UserRole.functional_user: True,    # the role's whole point is day-to-day trading
    UserRole.quant_analyst: False,     # research role; grant explicitly if they also need to trade
}


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    active: bool
    can_trade: bool
    auth_provider: str
    created_at: str


class UserCreateIn(BaseModel):
    email: str
    password: str
    role: UserRole
    can_trade: Optional[bool] = None  # None = role default (see _DEFAULT_CAN_TRADE)


class UserUpdateIn(BaseModel):
    role: Optional[UserRole] = None
    can_trade: Optional[bool] = None
    active: Optional[bool] = None
    password: Optional[str] = None    # None/"" = leave the stored password unchanged


def _out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, role=u.role.value, active=u.active,
                   can_trade=u.can_trade, auth_provider=u.auth_provider,
                   created_at=u.created_at.isoformat())


def _require_assignable(role: UserRole) -> None:
    if role not in ASSIGNABLE_ROLES:
        raise HTTPException(status_code=400,
                            detail=f"role must be one of {[r.value for r in ASSIGNABLE_ROLES]}")


def _remaining_admin_count(session: Session, tenant_id: int, excluding_user_id: Optional[int] = None) -> int:
    rows = session.exec(select(User).where(
        User.tenant_id == tenant_id, User.role == UserRole.tenant_admin, User.active)).all()
    return len([r for r in rows if r.id != excluding_user_id])


@router.get("", response_model=list[UserOut])
def list_users(admin: CurrentUser = Depends(require_admin), session: Session = Depends(get_session)):
    rows = session.exec(
        select(User).where(User.tenant_id == admin.tenant_id).order_by(User.email)).all()
    return [_out(u) for u in rows]


@router.post("", response_model=UserOut)
def create_user(body: UserCreateIn, admin: CurrentUser = Depends(require_admin),
                session: Session = Depends(get_session)):
    _require_assignable(body.role)
    if session.exec(select(User).where(User.email == body.email)).first():
        raise HTTPException(status_code=409, detail="a user with that email already exists")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")

    can_trade = body.can_trade if body.can_trade is not None else _DEFAULT_CAN_TRADE[body.role]
    user = User(tenant_id=admin.tenant_id, email=body.email,
               password_hash=hash_password(body.password), role=body.role, can_trade=can_trade)
    session.add(user)
    session.commit()
    session.refresh(user)
    log_event(session, admin.email, "user_created", target=f"user:{user.id}",
             detail=f"{user.email} role={user.role.value}", tenant_id=admin.tenant_id)
    return _out(user)


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdateIn, admin: CurrentUser = Depends(require_admin),
                session: Session = Depends(get_session)):
    user = session.get(User, user_id)
    if user is None or user.tenant_id != admin.tenant_id:
        raise HTTPException(status_code=404, detail="user not found")

    demoting_self = user.id == admin.user_id and body.role is not None and body.role != UserRole.tenant_admin
    deactivating_self = user.id == admin.user_id and body.active is False
    if (demoting_self or deactivating_self) and _remaining_admin_count(session, admin.tenant_id, excluding_user_id=admin.user_id) == 0:
        raise HTTPException(status_code=400,
                            detail="can't remove your own admin access - you're the only admin left in this "
                                   "tenant. Promote another user to Admin first.")

    if body.role is not None:
        _require_assignable(body.role)
        user.role = body.role
    if body.can_trade is not None:
        user.can_trade = body.can_trade
    if body.active is not None:
        user.active = body.active
    if body.password:
        if len(body.password) < 8:
            raise HTTPException(status_code=400, detail="password must be at least 8 characters")
        user.password_hash = hash_password(body.password)

    session.add(user)
    session.commit()
    session.refresh(user)
    log_event(session, admin.email, "user_updated", target=f"user:{user.id}",
             detail=f"{user.email} role={user.role.value} active={user.active}", tenant_id=admin.tenant_id)
    return _out(user)
