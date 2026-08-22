"""
feedhandlers.py (router) - the admin portal's side of the C++ feed-handler
platform (data-plane/feedhandler-cpp/): browse the provider catalog
(app.feedhandler_catalog), activate a provider with its config + secrets,
and fetch the fully-resolved (decrypted) config an operator hands to a real
engine deployment via FH_CONFIG_JSON/FH_SECRETS_JSON_INLINE.

This is deliberately NOT "click activate and a new container appears" -
this codebase doesn't have live docker-orchestration-from-the-API anywhere
else either (TickHouse provisioning queues a Command for the tenant's OWN
fleet agent to render+apply, the same pull-based shape). What this DOES
make real: a tenant admin can browse available protocols/venues, fill in a
config form instead of hand-writing JSON, and store credentials encrypted
at rest instead of in a plaintext file on whichever box runs the engine -
see FeedHandlerInstance's own docstring.

Read access (list/catalog) is open to any authenticated user within the
tenant; secrets are NEVER included in list/get responses, only in
engine-config (see that endpoint's own docstring) - same require_admin
gating InfraProfile/TickHouse/Connector mutations already use.
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import crypto
from ..feedhandler_catalog import FEEDHANDLER_CATALOG, find as find_catalog_entry
from ..db import get_session, log_event
from ..models import FeedHandlerInstance, TickHouse
from .auth import CurrentUser, require_admin, require_tenant_scope

router = APIRouter(prefix="/feedhandlers", tags=["feedhandlers"])


class FeedHandlerOut(BaseModel):
    id: int
    tickhouse_id: int | None
    provider: str
    feed: str
    display_name: str
    enabled: bool
    config: dict
    has_secrets: bool
    status: str
    last_error: str
    created_at: str
    updated_at: str
    updated_by: str


class FeedHandlerIn(BaseModel):
    provider: str
    feed: str
    display_name: str = ""
    enabled: bool = False
    config: dict = {}
    secrets: dict = {}   # {"username": "...", "password": "..."} - encrypted before storage, see _encrypt_secrets
    tickhouse_id: int | None = None  # which TickHouse this feed publishes into - see FeedHandlerInstance's own docstring


def _out(row: FeedHandlerInstance) -> FeedHandlerOut:
    return FeedHandlerOut(
        id=row.id, tickhouse_id=row.tickhouse_id, provider=row.provider, feed=row.feed,
        display_name=row.display_name, enabled=row.enabled, config=json.loads(row.config_json or "{}"),
        has_secrets=bool(row.secrets_json), status=row.status, last_error=row.last_error,
        created_at=row.created_at.isoformat(), updated_at=row.updated_at.isoformat(),
        updated_by=row.updated_by)


def _get_scoped(session: Session, tenant_id: int, instance_id: int) -> FeedHandlerInstance:
    row = session.get(FeedHandlerInstance, instance_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="feed handler instance not found")
    return row


def _encrypt_secrets(secrets: dict) -> str:
    if not secrets:
        return ""
    return json.dumps({k: crypto.encrypt_secret(str(v)) for k, v in secrets.items()})


def _validate_tickhouse(session: Session, tenant_id: int, tickhouse_id: int | None) -> None:
    if tickhouse_id is None:
        return
    row = session.get(TickHouse, tickhouse_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail=f"tickhouse {tickhouse_id} not found")


def build_feed_handler_row(session: Session, tenant_id: int, updated_by: str, provider: str, feed: str,
                           display_name: str = "", enabled: bool = False, config: dict | None = None,
                           secrets: dict | None = None, tickhouse_id: int | None = None) -> FeedHandlerInstance:
    """Shared validation + row construction, used both by this router's own
    POST /feedhandlers and by routers/tickhouse.py's create() when a
    HighLevelSpec includes a feed_handler - "associate a feed handler with
    a TickHouse while creating the TickHouse" needs the exact same checks
    (known provider/feed, required credentials present, tickhouse belongs
    to this tenant) whichever endpoint triggers it, so this is the one
    place that does them."""
    entry = find_catalog_entry(provider, feed)
    if entry is None:
        raise HTTPException(status_code=400,
                            detail=f"unknown provider/feed '{provider}/{feed}' - see GET /feedhandlers/catalog")
    if enabled and entry.get("engine_support") == "catalog_only":
        raise HTTPException(status_code=400,
                            detail=f"{provider}/{feed} is catalog-only - listed for future integration "
                                   f"but the engine has no working decoder for it yet (see its 'requires' "
                                   f"field). Create it disabled to track the plan; it can't be enabled.")
    secrets = secrets or {}
    missing = [f for f in entry["credentials_required"] if f not in secrets or not secrets[f]]
    if missing:
        raise HTTPException(status_code=400, detail=f"missing required credential(s): {', '.join(missing)}")
    _validate_tickhouse(session, tenant_id, tickhouse_id)

    return FeedHandlerInstance(
        tenant_id=tenant_id, tickhouse_id=tickhouse_id, provider=provider, feed=feed,
        display_name=display_name or entry["display_name"], enabled=enabled,
        config_json=json.dumps(config or entry["default_config"]),
        secrets_json=_encrypt_secrets(secrets), status="configured", updated_by=updated_by)


@router.get("/catalog")
def catalog(user: CurrentUser = Depends(require_tenant_scope)):
    """Available provider/feed combinations with their default (non-secret)
    config and which credential fields each needs - see
    app.feedhandler_catalog's own docstring. This is what the admin
    portal's "activate a source" picker renders."""
    return {"providers": FEEDHANDLER_CATALOG}


@router.get("", response_model=list[FeedHandlerOut])
def list_instances(tickhouse_id: int | None = None, user: CurrentUser = Depends(require_tenant_scope),
                   session: Session = Depends(get_session)):
    q = select(FeedHandlerInstance).where(FeedHandlerInstance.tenant_id == user.tenant_id)
    if tickhouse_id is not None:
        q = q.where(FeedHandlerInstance.tickhouse_id == tickhouse_id)
    rows = session.exec(q.order_by(FeedHandlerInstance.provider, FeedHandlerInstance.feed)).all()
    return [_out(r) for r in rows]


@router.post("", response_model=FeedHandlerOut)
def create_instance(body: FeedHandlerIn, user: CurrentUser = Depends(require_admin),
                    session: Session = Depends(get_session)):
    row = build_feed_handler_row(session, user.tenant_id, user.email, body.provider, body.feed,
                                 body.display_name, body.enabled, body.config, body.secrets, body.tickhouse_id)
    session.add(row)
    session.commit()
    session.refresh(row)
    log_event(session, user.email, "feedhandler_created", target=f"feedhandler:{row.id}",
             detail=f"{row.provider}/{row.feed}" + (f" -> tickhouse:{row.tickhouse_id}" if row.tickhouse_id else ""),
             tenant_id=user.tenant_id)
    return _out(row)


@router.put("/{instance_id}", response_model=FeedHandlerOut)
def update_instance(instance_id: int, body: FeedHandlerIn, user: CurrentUser = Depends(require_admin),
                    session: Session = Depends(get_session)):
    row = _get_scoped(session, user.tenant_id, instance_id)
    if body.enabled:
        entry = find_catalog_entry(row.provider, row.feed)
        if entry and entry.get("engine_support") == "catalog_only":
            raise HTTPException(status_code=400,
                                detail=f"{row.provider}/{row.feed} is catalog-only - listed for future "
                                       f"integration but the engine has no working decoder for it yet.")
    _validate_tickhouse(session, user.tenant_id, body.tickhouse_id)
    row.tickhouse_id = body.tickhouse_id
    row.display_name = body.display_name or row.display_name
    row.enabled = body.enabled
    if body.config:
        row.config_json = json.dumps(body.config)
    if body.secrets:
        row.secrets_json = _encrypt_secrets(body.secrets)
    row.updated_at = datetime.utcnow()
    row.updated_by = user.email
    session.add(row)
    session.commit()
    session.refresh(row)
    log_event(session, user.email, "feedhandler_updated", target=f"feedhandler:{row.id}",
             detail=f"{row.provider}/{row.feed} enabled={row.enabled}", tenant_id=user.tenant_id)
    return _out(row)


@router.delete("/{instance_id}")
def delete_instance(instance_id: int, user: CurrentUser = Depends(require_admin),
                    session: Session = Depends(get_session)):
    row = _get_scoped(session, user.tenant_id, instance_id)
    provider, feed = row.provider, row.feed
    session.delete(row)
    session.commit()
    log_event(session, user.email, "feedhandler_deleted", target=f"feedhandler:{instance_id}",
             detail=f"{provider}/{feed}", tenant_id=user.tenant_id)
    return {"status": "deleted"}


@router.get("/{instance_id}/engine-config")
def engine_config(instance_id: int, user: CurrentUser = Depends(require_admin),
                  session: Session = Depends(get_session)):
    """The fully-resolved config (decrypted secrets included) for a REAL
    engine deployment - what an operator saves as FH_CONFIG_JSON and passes
    the "secrets" object as FH_SECRETS_JSON_INLINE (see
    data-plane/feedhandler-cpp/main.cpp's live mode). require_admin-gated
    specifically because this is the one endpoint in this router that ever
    returns a decrypted secret value - every other response in this file
    only ever reports has_secrets: bool."""
    row = _get_scoped(session, user.tenant_id, instance_id)
    secrets = {}
    if row.secrets_json:
        encrypted = json.loads(row.secrets_json)
        secrets = {k: crypto.decrypt_secret(v) for k, v in encrypted.items()}
    return {"config": json.loads(row.config_json or "{}"), "secrets": secrets}
