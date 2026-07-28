"""
provisioning.py - just-in-time user provisioning shared by every federated
auth path (Entra OIDC and LDAP/AD).

On first federated login we create the user; on subsequent logins we refresh
their role and external id from the directory. Federated users never hold a
local password, so password login stays refused for them.
"""
from fastapi import HTTPException
from sqlmodel import Session, select

from .db import log_event
from .models import Tenant, User, UserRole


def provision_user(session: Session, tenant: Tenant, identity: dict, role: str,
                   provider: str) -> User:
    """Create-or-refresh the tenant user for a federated identity.

    identity: {"email", "name", "external_id", "memberships"} (memberships
    unused here - the caller has already resolved `role`). provider: "entra"
    or "ldap", recorded on the user.
    """
    email = identity["email"]
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        user = User(tenant_id=tenant.id, email=email, password_hash="",
                    role=UserRole(role), active=True, auth_provider=provider,
                    external_id=identity.get("external_id"))
        session.add(user)
        session.flush()
        log_event(session, user.email, "auth_provision", tenant.slug,
                  detail=f"provider={provider} role={role}", outcome="success", tenant_id=tenant.id)
    else:
        if user.tenant_id != tenant.id:
            raise HTTPException(403, detail="this email is already registered under a different tenant")
        if not user.active:
            raise HTTPException(403, detail="user is disabled")
        user.role = UserRole(role)
        user.external_id = identity.get("external_id")
        user.auth_provider = provider
        session.add(user)
        log_event(session, user.email, "auth_login", tenant.slug,
                  detail=f"provider={provider} role={role}", outcome="success", tenant_id=tenant.id)
    session.commit()
    session.refresh(user)
    return user
