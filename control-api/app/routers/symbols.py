"""
symbols.py (router) - the symbol reference the pickers use (shard assignment,
query filters). Seeded across major markets, plus whatever app/symbol_discovery.py's
background poll loop has found actually trading (see app/symbols.py).
"""
from fastapi import APIRouter, Depends
from sqlmodel import Session

from .. import asset_metadata
from .. import symbols as symref
from ..db import get_session
from .auth import CurrentUser, require_tenant_scope

router = APIRouter(prefix="/symbols", tags=["symbols"])


@router.get("/markets")
def list_markets(user: CurrentUser = Depends(require_tenant_scope)):
    return {"markets": symref.markets()}


@router.get("/asset-classes")
def asset_classes(session: Session = Depends(get_session),
                  user: CurrentUser = Depends(require_tenant_scope)):
    """Breakdown of the persisted, live-discovered asset universe by class
    (equity/crypto/fx/unknown) - the durable counterpart to /symbols/markets,
    reflecting app/asset_metadata.py's canonical table rather than the
    in-memory-only live cache."""
    return {"asset_classes": asset_metadata.class_breakdown(session)}


@router.get("/search")
def search(q: str = "", market: str = "", limit: int = 25,
           user: CurrentUser = Depends(require_tenant_scope)):
    return {"symbols": symref.search(q, market=market or None, limit=limit),
            "note": (f"{len(symref.SYMBOLS)} seeded across major markets + "
                    f"{symref.live_count()} discovered live from incoming trade data")}
