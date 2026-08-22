"""
infra_profiles.py (router) - per-tenant, admin-managed bundles of NON-SECRET
infrastructure coordinates (region, VPC/subnet, namespace, storage class,
...) that TickHouse creation can auto-load instead of an admin retyping the
same region/VPC/namespace for every new cluster.

Tenant-scoped like TickHouse/Agent (each tenant deploys into its OWN cloud
accounts via its own fleet agents - see app.models.InfraProfile's own
docstring for why a global pool wouldn't fit that BYOC model). Read access
(list/meta) is open to any authenticated user WITHIN the tenant, since a
functional_user/quant_analyst still needs to see available profiles while
creating a TickHouse even if they can't edit them. All mutations are
require_admin (tenant_admin or platform_admin), matching TickHouse's own
provisioning-action gating.

Deliberately holds no credentials - see app.models.InfraProfile's own
docstring for why (this platform's fleet agent uses ambient cloud/cluster
identity, never credentials the control plane hands it). This is a settings
convenience, not a secret store.
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import tickhouse
from ..db import get_session, log_event
from ..models import InfraProfile
from .auth import CurrentUser, require_admin, require_tenant_scope

router = APIRouter(prefix="/infra-profiles", tags=["infra-profiles"])


class InfraProfileOut(BaseModel):
    id: int
    name: str
    description: str
    provider: str
    config: dict
    is_default: bool
    enabled: bool
    created_at: str
    updated_at: str
    updated_by: str


class InfraProfileIn(BaseModel):
    name: str
    description: str = ""
    provider: str
    config: dict = {}
    is_default: bool = False
    enabled: bool = True


def _out(p: InfraProfile) -> InfraProfileOut:
    return InfraProfileOut(
        id=p.id, name=p.name, description=p.description, provider=p.provider,
        config=json.loads(p.config_json or "{}"), is_default=p.is_default, enabled=p.enabled,
        created_at=p.created_at.isoformat(), updated_at=p.updated_at.isoformat(),
        updated_by=p.updated_by)


def _validate(provider: str, config: dict) -> None:
    if provider not in tickhouse.CLOUDS:
        raise HTTPException(status_code=400,
                            detail=f"provider must be one of {tickhouse.CLOUDS}")
    allowed = set(tickhouse.config_fields(provider))
    unknown = set(config.keys()) - allowed
    if unknown:
        raise HTTPException(status_code=400,
                            detail=f"unknown field(s) for {provider}: {', '.join(sorted(unknown))} "
                                   f"(allowed: {', '.join(sorted(allowed))})")


def _get_scoped(session: Session, tenant_id: int, profile_id: int) -> InfraProfile:
    profile = session.get(InfraProfile, profile_id)
    if profile is None or profile.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="profile not found")
    return profile


@router.get("/meta")
def meta(user: CurrentUser = Depends(require_tenant_scope)):
    """Which providers exist and which fields each one collects - see
    app.tickhouse.CLOUD_CONFIG_FIELDS/KUBERNETES_CONFIG_FIELDS, the same
    source of truth the TickHouse creation wizard's own field list uses."""
    return {"providers": list(tickhouse.CLOUDS),
            "config_fields": {c: tickhouse.config_fields(c) for c in tickhouse.CLOUDS}}


@router.get("", response_model=list[InfraProfileOut])
def list_profiles(user: CurrentUser = Depends(require_tenant_scope),
                  session: Session = Depends(get_session)):
    rows = session.exec(
        select(InfraProfile).where(InfraProfile.tenant_id == user.tenant_id)
        .order_by(InfraProfile.provider, InfraProfile.name)).all()
    return [_out(p) for p in rows]


@router.post("", response_model=InfraProfileOut)
def create_profile(body: InfraProfileIn, user: CurrentUser = Depends(require_admin),
                   session: Session = Depends(get_session)):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    _validate(body.provider, body.config)
    if body.is_default:
        _clear_existing_default(session, user.tenant_id, body.provider)
    profile = InfraProfile(tenant_id=user.tenant_id, name=body.name.strip(), description=body.description,
                           provider=body.provider, config_json=json.dumps(body.config),
                           is_default=body.is_default, enabled=body.enabled,
                           updated_by=user.email)
    session.add(profile)
    session.commit()
    session.refresh(profile)
    log_event(session, user.email, "infra_profile_created", target=f"infra_profile:{profile.id}",
             detail=f"{profile.name} ({profile.provider})", tenant_id=user.tenant_id)
    return _out(profile)


@router.put("/{profile_id}", response_model=InfraProfileOut)
def update_profile(profile_id: int, body: InfraProfileIn,
                   user: CurrentUser = Depends(require_admin),
                   session: Session = Depends(get_session)):
    profile = _get_scoped(session, user.tenant_id, profile_id)
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    _validate(body.provider, body.config)
    if body.is_default and not (profile.is_default and profile.provider == body.provider):
        _clear_existing_default(session, user.tenant_id, body.provider)
    profile.name = body.name.strip()
    profile.description = body.description
    profile.provider = body.provider
    profile.config_json = json.dumps(body.config)
    profile.is_default = body.is_default
    profile.enabled = body.enabled
    profile.updated_at = datetime.utcnow()
    profile.updated_by = user.email
    session.add(profile)
    session.commit()
    session.refresh(profile)
    log_event(session, user.email, "infra_profile_updated", target=f"infra_profile:{profile.id}",
             detail=f"{profile.name} ({profile.provider})", tenant_id=user.tenant_id)
    return _out(profile)


@router.delete("/{profile_id}")
def delete_profile(profile_id: int, user: CurrentUser = Depends(require_admin),
                   session: Session = Depends(get_session)):
    profile = _get_scoped(session, user.tenant_id, profile_id)
    name, provider = profile.name, profile.provider
    session.delete(profile)
    session.commit()
    log_event(session, user.email, "infra_profile_deleted", target=f"infra_profile:{profile_id}",
             detail=f"{name} ({provider})", tenant_id=user.tenant_id)
    return {"status": "deleted"}


def _clear_existing_default(session: Session, tenant_id: int, provider: str) -> None:
    """At most one default profile per (tenant, provider) - demoting
    whichever one currently holds it before a new one takes over, rather
    than requiring the admin to remember to unset the old one first."""
    for existing in session.exec(
            select(InfraProfile).where(InfraProfile.tenant_id == tenant_id,
                                       InfraProfile.provider == provider,
                                       InfraProfile.is_default)).all():
        existing.is_default = False
        session.add(existing)
