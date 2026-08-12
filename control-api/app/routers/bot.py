"""
bot.py (router) - configure and monitor the server-side trade signal engine
(app/signal_engine.py, app/bot_scheduler.py).

This is the persisted, server-evaluated successor to web-ui/src/pages/Bot.jsx's
client-only bot: config/positions/activity log all live in the database, and
a background scheduler evaluates every enabled tenant's bot on an interval -
so it keeps running (and keeps watching its stop-losses) whether or not
anyone has the page open, and survives a browser tab closing or reloading.

Viewing is open to any tenant user, same as /trading/positions. Enabling the
bot, changing its config, or manually closing a position all require the
can_trade permission, same as placing a manual order.
"""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import oms
from .. import signal_engine
from ..db import get_session, log_event
from ..models import BotConfig, BotLogEntry, BotPosition
from .auth import CurrentUser, require_tenant_scope
from .trading import place_market_order_internal, require_trading

router = APIRouter(prefix="/bot", tags=["bot"])


def _get_or_create_config(session: Session, tenant_id: int) -> BotConfig:
    config = session.exec(select(BotConfig).where(BotConfig.tenant_id == tenant_id)).first()
    if config is None:
        config = BotConfig(tenant_id=tenant_id)
        session.add(config)
        session.commit()
        session.refresh(config)
    return config


def _config_api(c: BotConfig) -> dict:
    return {
        "enabled": c.enabled, "mode": c.mode,
        "symbols": json.loads(c.symbols_json or "[]"),
        "max_positions": c.max_positions, "paper_capital": c.paper_capital,
        "risk_pct": c.risk_pct, "stop_loss_pct": c.stop_loss_pct,
        "max_risk_pct": signal_engine.MAX_RISK_PCT, "max_basket": signal_engine.MAX_BASKET,
        "max_positions_cap": signal_engine.MAX_POSITIONS_CAP,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "updated_by": c.updated_by,
    }


@router.get("/config")
def get_config(user: CurrentUser = Depends(require_tenant_scope),
              session: Session = Depends(get_session)):
    return _config_api(_get_or_create_config(session, user.tenant_id))


class ConfigBody(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[str] = None
    symbols: Optional[list] = None
    max_positions: Optional[int] = None
    paper_capital: Optional[float] = None
    risk_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None


@router.put("/config")
def put_config(body: ConfigBody, user: CurrentUser = Depends(require_trading),
              session: Session = Depends(get_session)):
    config = _get_or_create_config(session, user.tenant_id)

    if body.mode is not None:
        if body.mode not in ("manual", "auto"):
            raise HTTPException(status_code=400, detail="mode must be 'manual' or 'auto'")
        config.mode = body.mode
    if body.symbols is not None:
        capped = [str(s).upper() for s in body.symbols][:signal_engine.MAX_BASKET]
        # Removing a symbol the bot currently holds a position in would
        # orphan that position from monitoring - same guard Bot.jsx applied
        # client-side (handleBasketChange), enforced here so it holds
        # regardless of which client is calling this.
        held = {p.symbol for p in session.exec(
            select(BotPosition).where(BotPosition.tenant_id == user.tenant_id)).all()}
        removed = held - set(capped)
        if removed:
            raise HTTPException(status_code=400,
                                detail=f"can't remove {', '.join(sorted(removed))} from the basket "
                                       f"while it has an open position - close it first")
        config.symbols_json = json.dumps(capped)
    if body.max_positions is not None:
        config.max_positions = min(signal_engine.MAX_POSITIONS_CAP, max(1, body.max_positions))
    if body.paper_capital is not None:
        config.paper_capital = max(0.0, body.paper_capital)
    if body.risk_pct is not None:
        # Hard cap enforced server-side regardless of what's posted. This
        # used to be a client-only cap (web-ui/src/pages/Bot.jsx) - any
        # direct API call could bypass it; validating here, on the row the
        # server-side engine actually reads, closes that gap for real.
        config.risk_pct = min(signal_engine.MAX_RISK_PCT, max(0.0, body.risk_pct))
    if body.stop_loss_pct is not None:
        config.stop_loss_pct = max(0.1, body.stop_loss_pct)
    if body.enabled is not None:
        if body.enabled and config.mode == "manual" and not json.loads(config.symbols_json or "[]"):
            raise HTTPException(status_code=400,
                                detail="add at least one symbol to the basket before enabling")
        config.enabled = body.enabled

    config.updated_at = datetime.utcnow()
    config.updated_by = user.email
    session.add(config)
    log_event(session, user.email, "bot_config_updated", target=f"tenant:{user.tenant_id}",
              detail=f"enabled={config.enabled} mode={config.mode}", tenant_id=user.tenant_id)
    session.refresh(config)
    return _config_api(config)


@router.get("/positions")
def positions(user: CurrentUser = Depends(require_tenant_scope),
             session: Session = Depends(get_session)):
    rows = session.exec(select(BotPosition).where(BotPosition.tenant_id == user.tenant_id)).all()
    return [{"symbol": p.symbol, "qty": p.qty, "entry_price": p.entry_price,
            "stop_price": p.stop_price, "order_id": p.order_id,
            "opened_at": p.opened_at.isoformat() if p.opened_at else None} for p in rows]


@router.get("/log")
def bot_log(limit: int = 60, user: CurrentUser = Depends(require_tenant_scope),
           session: Session = Depends(get_session)):
    limit = max(1, min(limit, 200))
    rows = session.exec(select(BotLogEntry).where(BotLogEntry.tenant_id == user.tenant_id)
                        .order_by(BotLogEntry.timestamp.desc()).limit(limit)).all()
    return [{"symbol": e.symbol, "type": e.type, "reason": e.reason,
            "ts": e.timestamp.isoformat() if e.timestamp else None} for e in rows]


@router.post("/positions/{symbol}/close")
def close_position(symbol: str, user: CurrentUser = Depends(require_trading),
                   session: Session = Depends(get_session)):
    """Manually flatten a bot-opened position - the server-side counterpart
    to Bot.jsx's closePositionManually. Same rationale as that button's own
    comment: "stop bot" only pauses the poll loop, it deliberately does not
    force-liquidate, so this is the explicit per-symbol way to flatten."""
    symbol = symbol.upper()
    pos = session.exec(select(BotPosition).where(
        BotPosition.tenant_id == user.tenant_id, BotPosition.symbol == symbol)).first()
    if pos is None:
        raise HTTPException(status_code=404, detail=f"no open bot position in {symbol}")

    tape = signal_engine.fetch_trade_tape([symbol])
    rows = tape.get(symbol, [])
    last = rows[-1]["price"] if rows else pos.entry_price

    try:
        order = place_market_order_internal(session, user.tenant_id, user.email, symbol, "sell",
                                            pos.qty, last)
    except oms.OrderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    fill_price = order.fill_price or last
    pnl = (fill_price - pos.entry_price) * pos.qty
    session.delete(pos)
    session.add(BotLogEntry(tenant_id=user.tenant_id, symbol=symbol,
                            type="close-win" if pnl >= 0 else "close-loss",
                            reason=f"manually closed - sold {pos.qty} @ {fill_price:.4f}, P&L {pnl:.2f}"))
    session.commit()
    return {"symbol": symbol, "qty": pos.qty, "fill_price": fill_price, "pnl": pnl}
