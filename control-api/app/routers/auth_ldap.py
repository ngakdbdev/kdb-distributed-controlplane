"""
auth_ldap.py - LDAP / on-prem Active Directory login and per-tenant config.

  POST /auth/ldap/{slug}/login    {username, password} -> session JWT
  GET  /auth/ldap/{slug}/config   current config (bind_password redacted)
  PUT  /auth/ldap/{slug}/config   configure the tenant's LDAP/AD binding

Unlike Entra (a browser redirect flow), LDAP is a direct credential check, so
this is a plain JSON POST the login form can call.
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import ldap_auth
from ..config import settings
from ..crypto import encrypt_secret
from ..db import get_session, log_event
from ..models import Tenant, TenantLDAP, UserRole
from ..provisioning import provision_user
from ..security import create_access_token
from .auth import CurrentUser, TokenResponse, get_current_user

router = APIRouter(prefix="/auth/ldap", tags=["ldap"])


def _tenant(slug: str, session: Session) -> Tenant:
    t = session.exec(select(Tenant).where(Tenant.slug == slug)).first()
    if t is None:
        raise HTTPException(404, detail=f"unknown tenant '{slug}'")
    return t


def _ldap_row(tenant: Tenant, session: Session) -> TenantLDAP:
    row = session.exec(select(TenantLDAP).where(TenantLDAP.tenant_id == tenant.id)).first()
    if row is None:
        raise HTTPException(404, detail=f"no LDAP directory configured for '{tenant.slug}'")
    return row


# --------------------------------------------------------------------- login
class LDAPLoginRequest(BaseModel):
    username: str
    password: str


@router.post("/{slug}/login", response_model=TokenResponse)
def ldap_login(slug: str, body: LDAPLoginRequest, session: Session = Depends(get_session)):
    tenant = _tenant(slug, session)
    row = _ldap_row(tenant, session)
    if not row.enabled:
        raise HTTPException(400, detail=f"LDAP is not enabled for '{slug}'")

    cfg = ldap_auth.LDAPConfig.from_model(slug, row)
    try:
        identity = ldap_auth.authenticate(cfg, body.username, body.password)
    except ldap_auth.LDAPAuthError:
        log_event(session, body.username, "ldap_login", slug,
                  detail="authentication failed", outcome="failure", tenant_id=tenant.id)
        raise HTTPException(401, detail="invalid credentials")

    if not identity["email"]:
        raise HTTPException(401, detail="directory entry has no email/UPN attribute to provision from")
    if not ldap_auth.email_allowed(identity["email"], cfg.allowed_domains):
        log_event(session, identity["email"], "ldap_login", slug,
                  detail="email domain not allowed", outcome="failure", tenant_id=tenant.id)
        raise HTTPException(403, detail="your email domain is not permitted for this tenant")

    role = ldap_auth.resolve_role(identity["memberships"], cfg.group_role_map, cfg.default_role)
    user = provision_user(session, tenant, identity, role, "ldap")
    token = create_access_token(user.id, user.email, user.role.value, user.tenant_id)
    return TokenResponse(access_token=token, role=user.role.value, tenant_id=user.tenant_id)


# --------------------------------------------------------------------- config
class LDAPConfigIn(BaseModel):
    server_uri: str
    use_start_tls: bool = False
    bind_mode: str = "search"                    # "search" / "direct"
    bind_dn: str = ""
    bind_password: Optional[str] = None          # omit on update to keep the stored one
    user_search_base: str = ""
    user_filter: str = "(sAMAccountName={username})"
    bind_dn_template: str = "{username}"
    attr_email: str = "mail"
    attr_name: str = "displayName"
    group_attr: str = "memberOf"
    group_role_map: dict = {}
    default_role: str = "tenant_admin"
    allowed_domains: str = ""
    enabled: bool = False


class LDAPConfigOut(BaseModel):
    tenant_slug: str
    server_uri: str
    use_start_tls: bool
    bind_mode: str
    bind_dn: str
    bind_password_set: bool                       # never return the secret
    user_search_base: str
    user_filter: str
    bind_dn_template: str
    attr_email: str
    attr_name: str
    group_attr: str
    group_role_map: dict
    default_role: str
    allowed_domains: str
    enabled: bool
    login_url: str


def _require_config_admin(slug: str, session: Session, user: CurrentUser) -> Tenant:
    tenant = _tenant(slug, session)
    if user.role == "platform_admin":
        return tenant
    if user.role == "tenant_admin" and user.tenant_id == tenant.id:
        return tenant
    raise HTTPException(403, detail="not permitted to manage LDAP for this tenant")


@router.put("/{slug}/config", response_model=LDAPConfigOut)
def put_config(slug: str, body: LDAPConfigIn, user: CurrentUser = Depends(get_current_user),
               session: Session = Depends(get_session)):
    tenant = _require_config_admin(slug, session, user)
    if body.bind_mode not in ("search", "direct"):
        raise HTTPException(400, detail="bind_mode must be 'search' or 'direct'")
    if body.default_role != "tenant_admin":
        raise HTTPException(400, detail="default_role must be tenant_admin (platform_admin cannot be granted via tenant LDAP)")

    row = session.exec(select(TenantLDAP).where(TenantLDAP.tenant_id == tenant.id)).first()
    if row is None:
        row = TenantLDAP(tenant_id=tenant.id)
        session.add(row)

    row.server_uri = body.server_uri
    row.use_start_tls = body.use_start_tls
    row.bind_mode = body.bind_mode
    row.bind_dn = body.bind_dn
    if body.bind_password:                        # only overwrite when a new secret is supplied
        row.bind_password = encrypt_secret(body.bind_password)
    row.user_search_base = body.user_search_base
    row.user_filter = body.user_filter
    row.bind_dn_template = body.bind_dn_template
    row.attr_email = body.attr_email
    row.attr_name = body.attr_name
    row.group_attr = body.group_attr
    row.group_role_map = json.dumps(body.group_role_map)
    row.default_role = UserRole(body.default_role)
    row.allowed_domains = body.allowed_domains
    row.enabled = body.enabled
    session.commit()
    session.refresh(row)
    log_event(session, user.email, "ldap_config", slug,
              detail=f"enabled={row.enabled} server={row.server_uri}", tenant_id=tenant.id)
    return _config_out(slug, row)


@router.get("/{slug}/config", response_model=LDAPConfigOut)
def get_config(slug: str, user: CurrentUser = Depends(get_current_user),
               session: Session = Depends(get_session)):
    tenant = _require_config_admin(slug, session, user)
    row = _ldap_row(tenant, session)
    return _config_out(slug, row)


def _config_out(slug: str, row: TenantLDAP) -> LDAPConfigOut:
    try:
        gmap = json.loads(row.group_role_map or "{}")
    except json.JSONDecodeError:
        gmap = {}
    return LDAPConfigOut(
        tenant_slug=slug, server_uri=row.server_uri, use_start_tls=row.use_start_tls,
        bind_mode=row.bind_mode, bind_dn=row.bind_dn, bind_password_set=bool(row.bind_password),
        user_search_base=row.user_search_base, user_filter=row.user_filter,
        bind_dn_template=row.bind_dn_template, attr_email=row.attr_email, attr_name=row.attr_name,
        group_attr=row.group_attr, group_role_map=gmap, default_role=row.default_role.value,
        allowed_domains=row.allowed_domains, enabled=row.enabled,
        login_url=f"{settings.public_base_url.rstrip('/')}/auth/ldap/{slug}/login",
    )
