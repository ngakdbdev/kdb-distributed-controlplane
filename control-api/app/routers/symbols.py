"""
symbols.py (router) - the symbol reference the pickers use (shard assignment,
query filters). Seeded across major markets, plus whatever app/symbol_discovery.py's
background poll loop has found actually trading (see app/symbols.py).
"""
from fastapi import APIRouter, Depends

from .. import symbols as symref
from .auth import CurrentUser, require_tenant_scope

router = APIRouter(prefix="/symbols", tags=["symbols"])


@router.get("/markets")
def list_markets(user: CurrentUser = Depends(require_tenant_scope)):
    return {"markets": symref.markets()}


@router.get("/search")
def search(q: str = "", market: str = "", limit: int = 25,
           user: CurrentUser = Depends(require_tenant_scope)):
    return {"symbols": symref.search(q, market=market or None, limit=limit),
            "note": (f"{len(symref.SYMBOLS)} seeded across major markets + "
                    f"{symref.live_count()} discovered live from incoming trade data")}
