"""
auth_sso.py - Microsoft Entra (OIDC) login endpoints and per-tenant SSO config.

  GET  /auth/sso/{slug}/login     -> 302 redirect to the tenant's Entra
  GET  /auth/sso/{slug}/callback  -> validate, JIT-provision, issue a session JWT
  GET  /auth/sso/{slug}/config    -> current config (client_secret redacted)
  PUT  /auth/sso/{slug}/config    -> configure the tenant's Entra federation
"""
import json
import secrets
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import sso
from ..config import settings
from ..db import get_session, log_event
from ..models import Tenant, TenantIdP, User, UserRole
from ..provisioning import provision_user
from ..security import create_access_token
from .auth import CurrentUser, TokenResponse, get_current_user

router = APIRouter(prefix="/auth/sso", tags=["sso"])

STATE_COOKIE = "kdb_sso_state"


def _tenant(slug: str, session: Session) -> Tenant:
    t = session.exec(select(Tenant).where(Tenant.slug == slug)).first()
    if t is None:
        raise HTTPException(404, detail=f"unknown tenant '{slug}'")
    return t


def _idp(tenant: Tenant, session: Session) -> TenantIdP:
    idp = session.exec(select(TenantIdP).where(TenantIdP.tenant_id == tenant.id)).first()
    if idp is None:
        raise HTTPException(404, detail=f"no identity provider configured for '{tenant.slug}'")
    return idp


def _redirect_uri(slug: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/auth/sso/{slug}/callback"


def _secure_cookies() -> bool:
    return urlparse(settings.public_base_url).scheme == "https"


# --------------------------------------------------------------------- login
@router.get("/{slug}/login")
def sso_login(slug: str, response: Response, session: Session = Depends(get_session)):
    tenant = _tenant(slug, session)
    idp = _idp(tenant, session)
    if not idp.enabled:
        raise HTTPException(400, detail=f"SSO is not enabled for '{slug}'")

    cfg = sso.OIDCConfig.from_idp(slug, idp)
    discovery = sso.get_discovery(cfg.authority)

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = sso.new_pkce()

    state_cookie = sso.sign_state({"state": state, "nonce": nonce,
                                   "verifier": verifier, "slug": slug})
    url = sso.authorization_url(discovery, cfg, _redirect_uri(slug), state, nonce, challenge)

    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(STATE_COOKIE, state_cookie, max_age=settings.sso_state_ttl_sec,
                    httponly=True, secure=_secure_cookies(), samesite="lax")
    return resp


# --------------------------------------------------------------------- callback
@router.get("/{slug}/callback")
def sso_callback(slug: str, request: Request, state: str = "", code: str = "",
                 error: str = "", error_description: str = "", format: str = "",
                 session: Session = Depends(get_session)):
    if error:
        raise HTTPException(400, detail=f"identity provider returned error: {error} {error_description}".strip())

    cookie = request.cookies.get(STATE_COOKIE)
    if not cookie:
        raise HTTPException(400, detail="missing SSO state cookie (login not initiated here or it expired)")
    try:
        st = sso.verify_state(cookie)
    except Exception:
        raise HTTPException(400, detail="invalid or expired SSO state")
    if st.get("slug") != slug or not state or not secrets.compare_digest(st.get("state", ""), state):
        raise HTTPException(400, detail="SSO state mismatch (possible CSRF)")

    tenant = _tenant(slug, session)
    idp = _idp(tenant, session)
    cfg = sso.OIDCConfig.from_idp(slug, idp)
    discovery = sso.get_discovery(cfg.authority)

    tokens = sso.exchange_code(discovery, cfg, _redirect_uri(slug), code, st["verifier"])
    if "id_token" not in tokens:
        raise HTTPException(401, detail=f"token exchange failed: {tokens.get('error_description', tokens.get('error', 'no id_token'))}")

    try:
        claims = sso.validate_id_token(discovery, cfg, tokens["id_token"], st["nonce"])
    except Exception as exc:
        raise HTTPException(401, detail=f"id_token validation failed: {exc}")

    identity = sso.extract_identity(claims)
    if not identity["email"]:
        raise HTTPException(401, detail="no email/upn claim in id_token")
    if not sso.email_allowed(identity["email"], cfg.allowed_domains):
        log_event(session, identity["email"], "sso_login", slug,
                  detail="email domain not allowed", outcome="failure", tenant_id=tenant.id)
        raise HTTPException(403, detail="your email domain is not permitted for this tenant")

    role = sso.resolve_role(identity["memberships"], cfg.group_role_map, cfg.default_role)
    user = provision_user(session, tenant, identity, role, "entra")

    token = create_access_token(user.id, user.email, user.role.value, user.tenant_id)
    if format == "json":                       # convenience for programmatic clients / tests
        return TokenResponse(access_token=token, role=user.role.value, tenant_id=user.tenant_id)
    dest = f"{settings.web_ui_url.rstrip('/')}/#access_token={token}&token_type=bearer"
    resp = RedirectResponse(dest, status_code=302)
    resp.delete_cookie(STATE_COOKIE)
    return resp


# --------------------------------------------------------------------- config
class IdPConfigIn(BaseModel):
    provider: str = "entra"
    authority: str
    client_id: str
    client_secret: Optional[str] = None          # omit on update to keep the stored one
    allowed_domains: str = ""
    group_role_map: dict = {}
    default_role: str = "tenant_admin"
    enabled: bool = False


class IdPConfigOut(BaseModel):
    tenant_slug: str
    provider: str
    authority: str
    client_id: str
    client_secret_set: bool                      # never return the secret itself
    allowed_domains: str
    group_role_map: dict
    default_role: str
    enabled: bool
    login_url: str


def _require_config_admin(slug: str, session: Session, user: CurrentUser) -> Tenant:
    """Platform admins, or the tenant's own tenant_admin, may manage SSO config."""
    tenant = _tenant(slug, session)
    if user.role == "platform_admin":
        return tenant
    if user.role == "tenant_admin" and user.tenant_id == tenant.id:
        return tenant
    raise HTTPException(403, detail="not permitted to manage SSO for this tenant")


@router.put("/{slug}/config", response_model=IdPConfigOut)
def put_config(slug: str, body: IdPConfigIn, user: CurrentUser = Depends(get_current_user),
               session: Session = Depends(get_session)):
    tenant = _require_config_admin(slug, session, user)
    if body.default_role not in ("tenant_admin",):
        raise HTTPException(400, detail="default_role must be tenant_admin (platform_admin cannot be granted via tenant SSO)")

    idp = session.exec(select(TenantIdP).where(TenantIdP.tenant_id == tenant.id)).first()
    if idp is None:
        idp = TenantIdP(tenant_id=tenant.id)
        session.add(idp)

    idp.provider = body.provider
    idp.authority = body.authority.rstrip("/")
    idp.client_id = body.client_id
    if body.client_secret:                       # only overwrite when a new secret is supplied
        idp.client_secret = body.client_secret
    idp.allowed_domains = body.allowed_domains
    idp.group_role_map = json.dumps(body.group_role_map)
    idp.default_role = UserRole(body.default_role)
    idp.enabled = body.enabled
    session.commit()
    session.refresh(idp)
    log_event(session, user.email, "sso_config", slug,
              detail=f"enabled={idp.enabled} authority={idp.authority}", tenant_id=tenant.id)
    return _config_out(slug, idp)


@router.get("/{slug}/config", response_model=IdPConfigOut)
def get_config(slug: str, user: CurrentUser = Depends(get_current_user),
               session: Session = Depends(get_session)):
    tenant = _require_config_admin(slug, session, user)
    idp = _idp(tenant, session)
    return _config_out(slug, idp)


def _config_out(slug: str, idp: TenantIdP) -> IdPConfigOut:
    try:
        gmap = json.loads(idp.group_role_map or "{}")
    except json.JSONDecodeError:
        gmap = {}
    return IdPConfigOut(
        tenant_slug=slug, provider=idp.provider, authority=idp.authority,
        client_id=idp.client_id, client_secret_set=bool(idp.client_secret),
        allowed_domains=idp.allowed_domains, group_role_map=gmap,
        default_role=idp.default_role.value, enabled=idp.enabled,
        login_url=f"{settings.public_base_url.rstrip('/')}/auth/sso/{slug}/login",
    )
