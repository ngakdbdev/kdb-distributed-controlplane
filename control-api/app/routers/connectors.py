import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session, log_event
from ..models import Connector
from ..orchestrator import orchestrator
from ..provider_catalog import PROVIDER_CATALOG
from .auth import CurrentUser, require_admin, require_tenant_scope

router = APIRouter(prefix="/connectors", tags=["connectors"])

# Which env var each connector's underlying feed simulator reads for a symbol
# universe override (both support --symbols/<VAR> - see build_universe() in
# data-plane/feeds/bpipe_sim.py and crims_sim.py). Keyed by service_name since
# that's the stable identifier already used to control the container; a real
# vendor connector slotting in behind the same on/off model would add its own
# entry here mapping to whatever env var IT reads for a symbol scope.
_SYMBOLS_ENV_VAR = {
    "bpipe-sim": "BPIPE_SYMBOLS",
    "crims-sim": "CRIMS_SYMBOLS",
}


def _with_symbols(c: Connector) -> dict:
    try:
        symbols = json.loads(c.symbols_json or "[]")
    except (TypeError, ValueError):
        symbols = []
    return {**c.model_dump(), "symbols": symbols}


@router.get("/providers")
def list_providers(user: CurrentUser = Depends(require_tenant_scope)):
    """Catalog of market-data provider adapters the UI can offer - which are
    live (usable with an API key) vs licensed (need a data agreement), and what
    each one requires. Canonical adapters live in data-plane/feeds/providers/."""
    return PROVIDER_CATALOG


@router.get("")
def list_connectors(user: CurrentUser = Depends(require_tenant_scope),
                     session: Session = Depends(get_session)):
    connectors = session.exec(select(Connector).where(Connector.tenant_id == user.tenant_id)).all()
    return [
        {**_with_symbols(c), "live_status": orchestrator.status(c.service_name)}
        for c in connectors
    ]


@router.post("/{connector_id}/toggle")
def toggle_connector(connector_id: int, user: CurrentUser = Depends(require_admin),
                      session: Session = Depends(get_session)):
    connector = session.get(Connector, connector_id)
    if connector is None or connector.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="connector not found")

    if connector.enabled:
        ok = orchestrator.stop(connector.service_name)
        action = "disable_connector"
        if ok:
            connector.enabled = False
    else:
        ok = orchestrator.start(connector.service_name)
        action = "enable_connector"
        if ok:
            connector.enabled = True

    session.add(connector)
    session.commit()
    session.refresh(connector)
    result = _with_symbols(connector)  # snapshot before log_event's commit expires it

    log_event(session, user.email, action, connector.name,
              outcome="success" if ok else "failure", tenant_id=user.tenant_id)
    return result


class SymbolsBody(BaseModel):
    symbols: list[str] = []   # [] = full built-in universe (default, unscoped)


@router.put("/{connector_id}/symbols")
def set_connector_symbols(connector_id: int, body: SymbolsBody,
                           user: CurrentUser = Depends(require_admin),
                           session: Session = Depends(get_session)):
    """Scope a connector to a symbol group. Persists immediately; if the
    connector is currently enabled and its feed supports a live symbol-scope
    env var (see _SYMBOLS_ENV_VAR), also recreates its container with that
    env var set so the change takes effect without a manual restart."""
    connector = session.get(Connector, connector_id)
    if connector is None or connector.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="connector not found")

    symbols = sorted({s.strip().upper() for s in body.symbols if s.strip()})
    connector.symbols_json = json.dumps(symbols)
    session.add(connector)
    session.commit()
    session.refresh(connector)
    result = _with_symbols(connector)

    env_var = _SYMBOLS_ENV_VAR.get(connector.service_name)
    live_applied = False
    if connector.enabled and env_var is not None:
        live_applied = orchestrator.set_env(connector.service_name, {env_var: ",".join(symbols)})
    result["live_applied"] = live_applied
    result["live_status"] = orchestrator.status(connector.service_name)

    log_event(session, user.email, "connector_symbols_updated", connector.name,
              detail=f"{len(symbols)} symbols, live_applied={live_applied}",
              outcome="success", tenant_id=user.tenant_id)
    return result
