"""
tradingview_webhook.py (router) - lets a tenant's TradingView alerts
(Pine Script strategies or manual alerts) place real orders through this
platform's OMS.

Two routers on purpose, with different trust boundaries:
  * `router` (prefix /tradingview) - normal authenticated config CRUD, same
    require_trading permission gate as everything else in trading.py/bot.py.
  * `webhook_router` (prefix /webhooks) - the INBOUND endpoint TradingView
    itself calls. TradingView's alert webhooks cannot send a custom header
    or a signed body on non-Enterprise plans, so there is no JWT here at
    all - the only credential is the `token` path segment, matched against
    TradingViewWebhook.token. Treat that token as a bearer secret: this
    endpoint deliberately does NOT sit behind get_current_user.

Every triggered order goes through place_market_order_internal - the exact
same pre-trade risk gate and order path routers/bot.py's auto-bot uses.
There is no webhook-specific shortcut around risk controls, same principle
as everywhere else in this codebase an automated surface places an order.

allowed_symbols is a hard allowlist (not just a UI suggestion) and enabling
requires at least one symbol configured, same "add a symbol before
enabling" guard routers/bot.py's put_config already enforces for the
signal bot - specifically so a leaked/guessed token can only trade symbols
the tenant explicitly wired up, not an arbitrary one typed into the alert.
"""
import json
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import oms
from .. import signal_engine
from ..db import get_session, log_event
from ..models import TradingViewWebhook
from .auth import CurrentUser, require_tenant_scope
from .trading import place_market_order_internal, require_trading

router = APIRouter(prefix="/tradingview", tags=["tradingview"])
webhook_router = APIRouter(prefix="/webhooks", tags=["webhooks"])

MAX_QTY_CAP = 100_000.0   # absolute ceiling regardless of what a tenant configures - a typo guard, not a real limit


def _get_or_create_config(session: Session, tenant_id: int) -> TradingViewWebhook:
    config = session.exec(select(TradingViewWebhook).where(
        TradingViewWebhook.tenant_id == tenant_id)).first()
    if config is None:
        config = TradingViewWebhook(tenant_id=tenant_id, token=secrets.token_urlsafe(32))
        session.add(config)
        session.commit()
        session.refresh(config)
    return config


def _config_api(c: TradingViewWebhook) -> dict:
    return {
        "enabled": c.enabled, "token": c.token,
        "allowed_symbols": json.loads(c.allowed_symbols_json or "[]"),
        "max_qty": c.max_qty,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "updated_by": c.updated_by,
        "last_triggered_at": c.last_triggered_at.isoformat() if c.last_triggered_at else None,
    }


@router.get("/config")
def get_config(user: CurrentUser = Depends(require_tenant_scope),
              session: Session = Depends(get_session)):
    return _config_api(_get_or_create_config(session, user.tenant_id))


class ConfigBody(BaseModel):
    enabled: Optional[bool] = None
    allowed_symbols: Optional[list] = None
    max_qty: Optional[float] = None


@router.put("/config")
def put_config(body: ConfigBody, user: CurrentUser = Depends(require_trading),
              session: Session = Depends(get_session)):
    config = _get_or_create_config(session, user.tenant_id)

    if body.allowed_symbols is not None:
        config.allowed_symbols_json = json.dumps([str(s).upper() for s in body.allowed_symbols])
    if body.max_qty is not None:
        config.max_qty = min(MAX_QTY_CAP, max(0.0, body.max_qty))
    if body.enabled is not None:
        if body.enabled and not json.loads(config.allowed_symbols_json or "[]"):
            raise HTTPException(status_code=400,
                                detail="add at least one symbol to the allowlist before enabling")
        config.enabled = body.enabled

    config.updated_at = datetime.utcnow()
    config.updated_by = user.email
    session.add(config)
    log_event(session, user.email, "tradingview_webhook_config_updated", target=f"tenant:{user.tenant_id}",
              detail=f"enabled={config.enabled} symbols={config.allowed_symbols_json}", tenant_id=user.tenant_id)
    session.refresh(config)
    return _config_api(config)


@router.post("/rotate")
def rotate_token(user: CurrentUser = Depends(require_trading),
                 session: Session = Depends(get_session)):
    """Issues a new token, invalidating the old one immediately - for a
    leaked URL (pasted somewhere public, a compromised TradingView account)
    or routine rotation. Any TradingView alert still configured with the
    old URL starts failing with 404 the moment this runs."""
    config = _get_or_create_config(session, user.tenant_id)
    config.token = secrets.token_urlsafe(32)
    config.updated_at = datetime.utcnow()
    config.updated_by = user.email
    session.add(config)
    log_event(session, user.email, "tradingview_webhook_token_rotated", target=f"tenant:{user.tenant_id}",
              tenant_id=user.tenant_id)
    session.commit()
    session.refresh(config)
    return _config_api(config)


# ---------------------------------------------------------------------------
# Inbound - no JWT, auth is the token path segment matching a stored row.

class WebhookOrderResult(BaseModel):
    accepted: bool
    detail: str = ""


def _last_price(symbol: str) -> Optional[float]:
    tape = signal_engine.fetch_trade_tape([symbol])
    rows = tape.get(symbol, [])
    return rows[-1]["price"] if rows else None


@webhook_router.post("/tradingview/{token}", response_model=WebhookOrderResult)
async def tradingview_webhook(token: str, request: Request, session: Session = Depends(get_session)):
    config = session.exec(select(TradingViewWebhook).where(TradingViewWebhook.token == token)).first()
    if config is None:
        # Same response whether the token is wrong or simply unconfigured -
        # doesn't tell an attacker probing tokens whether they're "close".
        raise HTTPException(status_code=404, detail="unknown webhook")
    if not config.enabled:
        raise HTTPException(status_code=403, detail="this webhook is disabled")

    raw = await request.body()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400,
                            detail='could not parse alert body as JSON - expected e.g. '
                                   '{"symbol": "{{ticker}}", "side": "{{strategy.order.action}}", '
                                   '"qty": "{{strategy.order.contracts}}"}')
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="alert body must be a JSON object")

    symbol = str(payload.get("symbol", "")).upper().strip()
    side = str(payload.get("side", "")).lower().strip()
    qty_raw = payload.get("qty")

    if not symbol:
        raise HTTPException(status_code=400, detail="payload missing 'symbol'")
    if side not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="payload 'side' must be 'buy' or 'sell'")

    allowed = json.loads(config.allowed_symbols_json or "[]")
    if symbol not in allowed:
        log_event(session, "tradingview-webhook", "tradingview_webhook_rejected",
                  target=f"tenant:{config.tenant_id}:{symbol}",
                  detail=f"symbol not in allowlist {allowed}", outcome="failure",
                  tenant_id=config.tenant_id)
        raise HTTPException(status_code=403, detail=f"{symbol} is not on this webhook's allowed_symbols list")

    try:
        qty = float(qty_raw) if qty_raw not in (None, "") else config.max_qty
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"could not parse 'qty' as a number: {qty_raw!r}")
    qty = min(qty, config.max_qty)
    if qty <= 0:
        raise HTTPException(status_code=400, detail="qty must be positive")

    ref_price = _last_price(symbol)
    if ref_price is None:
        log_event(session, "tradingview-webhook", "tradingview_webhook_rejected",
                  target=f"tenant:{config.tenant_id}:{symbol}",
                  detail="no recent trade price available for this symbol", outcome="failure",
                  tenant_id=config.tenant_id)
        raise HTTPException(status_code=422, detail=f"no recent trade price available for {symbol} - "
                                                     f"is a feed publishing it?")

    try:
        order = place_market_order_internal(session, config.tenant_id, "tradingview-webhook", symbol,
                                            side, qty, ref_price)
    except oms.OrderError as exc:
        log_event(session, "tradingview-webhook", "tradingview_webhook_order_failed",
                  target=f"tenant:{config.tenant_id}:{symbol}", detail=str(exc), outcome="failure",
                  tenant_id=config.tenant_id)
        raise HTTPException(status_code=400, detail=str(exc))

    config.last_triggered_at = datetime.utcnow()
    session.add(config)
    log_event(session, "tradingview-webhook", "tradingview_webhook_order_placed",
              target=f"tenant:{config.tenant_id}:{symbol}",
              detail=f"{side} {qty} {symbol} @ {order.fill_price} (order #{order.id})",
              tenant_id=config.tenant_id)
    session.commit()
    return WebhookOrderResult(accepted=True, detail=f"filled {side} {qty} {symbol} @ {order.fill_price}")
