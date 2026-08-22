"""
trading.py (router) - the subscriber trading terminal backend.

Viewing (market metrics, portfolio, greeks, forecast) is open to any tenant
user. PLACING ORDERS is gated behind the can_trade permission ("if permitted").
Orders run through the internal paper OMS by default (route='paper') -
unconfigured, this is unchanged from before real broker routing existed.
Set ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY + ALPACA_TRADING_MODE=paper to
route marketable fills through a real Alpaca account's paper-trading
simulation instead (see app/alpaca_broker.py); ALPACA_TRADING_MODE=live
additionally needs ALPACA_LIVE_TRADING_ACK set to an exact confirmation
phrase before real money can move - see that module's docstring for why
this is deliberately awkward to turn on. IBKR_TRADING_MODE (see
app/ibkr_broker.py) is the same pattern for Interactive Brokers, with its
own extra safety check since IBKR's paper/live distinction depends on which
account a locally-running gateway happens to be logged into, not just a
config flag - see that module. Configuring BOTH Alpaca and IBKR with a
non-off mode at once is treated as a misconfiguration and refuses every
order rather than silently picking one - see _router() below. A resting
(non-marketable) limit order always stays on the internal matcher
regardless of mode - see place_order's own comment on that boundary.
Forecasts are illustrative statistical projections, not advice (see
app/market.py).
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import alpaca_broker
from .. import greeks as gk
from .. import ibkr_broker
from .. import market as mkt
from .. import oms
from .. import portfolio as pf
from .. import risk_check
from ..db import get_session, log_event
from ..models import Order, Position, User
from .auth import CurrentUser, get_current_user, require_tenant_scope

router = APIRouter(prefix="/trading", tags=["trading"])

_PAPER = oms.PaperRouter()


def _router() -> oms.PaperRouter | oms.AlpacaRouter | oms.IBKRRouter:
    """Which router an order actually goes through - checked fresh on every
    call (not cached at import time) so *_TRADING_MODE/credentials can be
    verified live rather than baked in at process start. Falls back to the
    internal PaperRouter whenever neither broker is configured (mode
    'off') - each broker module's own client_from_env() already encodes
    that decision (including the live-mode double-confirmation check), so
    this function trusts it rather than re-deciding anything here.

    Configuring BOTH Alpaca and IBKR with a non-off mode simultaneously
    raises rather than picking one silently - there's no sane default
    priority between "the bot's orders go to Alpaca" and "...go to IBKR"
    when an operator has (almost certainly by accident) configured both;
    guessing wrong here means real orders going to the wrong broker
    entirely, so this fails loud and immediate instead."""
    alpaca_client = alpaca_broker.client_from_env()
    ibkr_client = ibkr_broker.client_from_env()
    if alpaca_client is not None and ibkr_client is not None:
        raise oms.OrderError(
            "both Alpaca and IBKR are configured with a non-off trading mode at the same time - "
            "this is refused rather than guessed at; set exactly one of ALPACA_TRADING_MODE / "
            "IBKR_TRADING_MODE to 'off' before placing orders")
    if alpaca_client is not None:
        return oms.AlpacaRouter(alpaca_client, live=alpaca_client.live)
    if ibkr_client is not None:
        return oms.IBKRRouter(ibkr_client, mode=ibkr_broker.trading_mode())
    return _PAPER


def require_trading(user: CurrentUser = Depends(get_current_user),
                    session: Session = Depends(get_session)) -> CurrentUser:
    """Admins may always trade; other users need the can_trade permission."""
    if user.tenant_id is None and user.role != "platform_admin":
        raise HTTPException(status_code=403, detail="no tenant scope")
    if user.role in ("platform_admin", "tenant_admin"):
        return user
    row = session.get(User, user.user_id)
    if not (row and row.can_trade):
        raise HTTPException(status_code=403,
                            detail="you don't have trading permission (ask an admin to grant it)")
    return user


def _check_pretrade_audited(symbol: str, actor: str, tenant_id: Optional[int],
                            session: Session, side: Optional[str] = None,
                            qty: Optional[float] = None,
                            ref_price: Optional[float] = None) -> Optional[str]:
    """risk_check.check_pretrade, plus: whenever the risk feed was unreachable
    (degraded=True - the decision used the fail-open/fail-closed policy
    rather than a verified read, either way), audit it. That's a materially
    different risk posture than a normal clean check and worth a record
    regardless of which way the policy resolved it. Takes a plain actor/
    tenant_id pair (not a CurrentUser) so the server-side signal engine
    (app/signal_engine.py), which has no HTTP request/JWT, can go through the
    exact same gate a human order does.

    side/qty/ref_price are optional and feed risk_check.check_portfolio_
    limits (daily loss / concentration) ALONGSIDE the per-symbol check
    above - skipped gracefully (not blocked) if not supplied or if
    tenant_id/ref_price is unavailable, same "don't block on missing
    optional data" posture as check_realized_volatility."""
    result = risk_check.check_pretrade(symbol)
    if result.degraded:
        log_event(
            session, actor=actor, action="risk_gate_degraded", target=symbol,
            detail=result.block_reason or "risk feed unreachable - failed open, order proceeded",
            outcome="blocked" if result.block_reason else "fail_open",
            tenant_id=tenant_id,
        )
    if result.block_reason:
        return result.block_reason
    if side is not None and qty is not None and ref_price is not None and tenant_id is not None:
        portfolio_result = risk_check.check_portfolio_limits(
            tenant_id, symbol, side, qty, ref_price, session)
        if portfolio_result.block_reason:
            log_event(session, actor=actor, action="portfolio_risk_blocked", target=symbol,
                      detail=portfolio_result.block_reason, outcome="blocked", tenant_id=tenant_id)
        return portfolio_result.block_reason
    return None


def place_market_order_internal(session: Session, tenant_id: int, actor: str, symbol: str,
                                 side: str, qty: float, ref_price: float,
                                 check_risk: bool = True) -> Order:
    """The market-order fill path (risk gate -> paper fill -> fold into
    position -> audit log), factored out so both the HTTP /orders endpoint
    below and the server-side signal engine (app/signal_engine.py) place
    orders through the identical path - a bot-placed order is never allowed
    to skip the same pre-trade risk check a human's would go through. Raises
    oms.OrderError (not HTTPException - this has no HTTP context) if the
    risk gate blocks it or the fill itself fails. `check_risk=False` is only
    for callers (place_order below) that already ran the gate a moment ago -
    it avoids double-querying the risk feed and double-logging a degraded
    check, not a way to skip the gate itself."""
    if check_risk:
        block_reason = _check_pretrade_audited(symbol, actor, tenant_id, session,
                                               side=side, qty=qty, ref_price=ref_price)
        if block_reason:
            raise oms.OrderError(block_reason)
    fill = _router().fill(side, qty, "market", ref_price, None, symbol=symbol)
    order = Order(tenant_id=tenant_id, user_email=actor, symbol=symbol, side=side.lower(),
                  qty=qty, order_type="market", status="filled", route=fill.route,
                  fill_price=fill.price)
    session.add(order)
    _fold_fill_into_position(session, tenant_id, symbol, side, qty, fill.price)
    session.commit()
    session.refresh(order)
    log_event(session, actor, "order_placed", f"{side} {qty} {symbol}",
              detail=f"{fill.route}@{fill.price}", tenant_id=tenant_id)
    return order


# ---- permission (so the UI can show/hide the order ticket) ---------------

@router.get("/permission")
def my_permission(user: CurrentUser = Depends(get_current_user),
                  session: Session = Depends(get_session)):
    row = session.get(User, user.user_id)
    can = user.role in ("platform_admin", "tenant_admin") or bool(row and row.can_trade)
    try:
        r = _router()
    except oms.OrderError as exc:
        # both Alpaca and IBKR configured at once (see _router()'s own
        # docstring) - a read-only permission check shouldn't 500 for this,
        # it should say so clearly so the misconfiguration is obvious
        # without having to first attempt a real order and hit a 400.
        return {"can_trade": can, "live_routing": False, "mode": "misconfigured", "error": str(exc)}
    return {"can_trade": can, "live_routing": r.route_name in ("alpaca-live", "ibkr-live"),
            "mode": r.route_name}


@router.get("/market-clock")
def market_clock(user: CurrentUser = Depends(get_current_user)):
    """Real Alpaca equities-session status (NYSE calendar via /v2/clock) -
    {'configured': False} if no Alpaca broker is set up (ALPACA_TRADING_MODE
    unset/off, or no credentials), regardless of role/permission - this is
    read-only status, same visibility as /permission above. Doesn't apply to
    crypto (Alpaca crypto trades 24/7) or to a deployment routing through
    IBKR instead - equities-only, Alpaca-only, by design of what /v2/clock
    itself reports."""
    try:
        return alpaca_broker.market_status()
    except alpaca_broker.AlpacaError as exc:
        raise HTTPException(status_code=502, detail=f"Alpaca clock unreachable: {exc}")


class GrantBody(BaseModel):
    email: str
    can_trade: bool = True


@router.post("/grant")
def grant(body: GrantBody, user: CurrentUser = Depends(require_tenant_scope),
          session: Session = Depends(get_session)):
    if user.role not in ("platform_admin", "tenant_admin"):
        raise HTTPException(status_code=403, detail="only admins can grant trading permission")
    target = session.exec(select(User).where(User.email == body.email)).first()
    if not target or (user.role == "tenant_admin" and target.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="user not found in your tenant")
    target.can_trade = body.can_trade
    session.add(target)
    session.commit()
    log_event(session, user.email, "trading_permission_set",
              body.email, detail=f"can_trade={body.can_trade}", tenant_id=user.tenant_id)
    return {"email": body.email, "can_trade": body.can_trade}


# ---- orders ---------------------------------------------------------------

class OrderBody(BaseModel):
    symbol: str
    side: str                      # buy / sell
    qty: float
    order_type: str = "market"     # market / limit
    limit_price: float | None = None
    ref_price: float | None = None  # current market price shown in the UI


def _fold_fill_into_position(session: Session, tenant_id: int, symbol: str,
                             side: str, qty: float, price: float) -> None:
    pos = session.exec(select(Position).where(Position.tenant_id == tenant_id,
                                              Position.symbol == symbol)).first()
    if pos is None:
        pos = Position(tenant_id=tenant_id, symbol=symbol, qty=0.0, avg_price=0.0)
    new_qty, new_avg, realized = oms.apply_to_position(pos.qty, pos.avg_price, side, qty, price)
    pos.qty, pos.avg_price = new_qty, new_avg
    pos.realized_pnl += realized
    pos.updated_at = datetime.utcnow()
    session.add(pos)


@router.post("/orders")
def place_order(body: OrderBody, user: CurrentUser = Depends(require_trading),
                session: Session = Depends(get_session)):
    if body.side.lower() not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="side must be buy or sell")
    if body.qty <= 0:
        raise HTTPException(status_code=400, detail="qty must be positive")
    symbol = body.symbol.upper()

    block_reason = _check_pretrade_audited(symbol, user.email, user.tenant_id, session,
                                           side=body.side, qty=body.qty,
                                           ref_price=body.ref_price or body.limit_price)
    if block_reason:
        raise HTTPException(status_code=400, detail=block_reason)

    # A market order always trades now. A limit order only trades now if it's
    # marketable (crosses the current reference price) - otherwise it rests as
    # a working order until /orders/match crosses it, or it's cancelled. There
    # is no order book here, so a marketable limit fills at its limit price
    # (the conservative assumption: never worse than what was asked for).
    marketable = body.order_type != "limit" or (
        body.ref_price is not None and body.limit_price is not None
        and oms.crosses(body.side, body.limit_price, body.ref_price)
    )

    if not marketable:
        if body.limit_price is None:
            raise HTTPException(status_code=400, detail="limit order needs a limit price")
        # A resting (non-marketable) limit order stays on the internal paper
        # matcher regardless of Alpaca configuration - see /orders/match.
        # Handing a genuinely-resting order to Alpaca would mean tracking ITS
        # order book lifecycle (partial fills, external cancellation, status
        # webhooks) instead of this codebase's own, which is real additional
        # scope this pass doesn't build; only orders that fill IMMEDIATELY
        # (market orders, and limit orders that cross on arrival, both
        # below) go through _router(). Tagging this with _PAPER.route_name
        # rather than _router().route_name keeps that boundary honest in the
        # audit trail - it never claims to have reached Alpaca.
        order = Order(tenant_id=user.tenant_id, user_email=user.email, symbol=symbol,
                      side=body.side.lower(), qty=body.qty, order_type=body.order_type,
                      limit_price=body.limit_price, status="new", route=_PAPER.route_name)
        session.add(order)
        session.commit()
        session.refresh(order)
        log_event(session, user.email, "order_placed",
                  f"{body.side} {body.qty} {symbol}",
                  detail=f"working limit@{body.limit_price}", tenant_id=user.tenant_id)
        return _order_api(order)

    # A marketable limit fills at its own limit price, not the reference
    # price - place_market_order_internal always fills at ref_price, which is
    # only correct for a plain market order, so that path is reused only for
    # the common (order_type == "market") case and the limit-fill kept here.
    # check_risk=False: the gate already ran a few lines up in this function.
    if body.order_type == "market":
        try:
            order = place_market_order_internal(
                session, user.tenant_id, user.email, symbol, body.side, body.qty, body.ref_price,
                check_risk=False)
        except oms.OrderError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return _order_api(order)

    try:
        fill = _router().fill(body.side, body.qty, body.order_type, body.ref_price, body.limit_price,
                              symbol=symbol)
    except oms.OrderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    order = Order(tenant_id=user.tenant_id, user_email=user.email, symbol=symbol,
                  side=body.side.lower(), qty=body.qty, order_type=body.order_type,
                  limit_price=body.limit_price, status="filled", route=fill.route,
                  fill_price=fill.price)
    session.add(order)
    _fold_fill_into_position(session, user.tenant_id, symbol, body.side, body.qty, fill.price)
    session.commit()
    session.refresh(order)
    log_event(session, user.email, "order_placed",
              f"{body.side} {body.qty} {symbol}",
              detail=f"{fill.route}@{fill.price}", tenant_id=user.tenant_id)
    return _order_api(order)


@router.get("/orders")
def list_orders(user: CurrentUser = Depends(require_tenant_scope),
                session: Session = Depends(get_session)):
    rows = session.exec(select(Order).where(Order.tenant_id == user.tenant_id)
                        .order_by(Order.created_at.desc())).all()
    return [_order_api(o) for o in rows]


class MatchBody(BaseModel):
    symbol: str
    price: float


@router.post("/orders/match")
def match_working_orders(body: MatchBody, user: CurrentUser = Depends(require_tenant_scope),
                         session: Session = Depends(get_session)):
    """Cross any working (status=new) limit orders for `symbol` against a
    fresh market price. Called opportunistically by the UI whenever it has a
    live price for the symbol it's showing - there's no separate matching
    engine process, so a resting order only ever gets a chance to fill when
    something asks about that symbol's price. Re-checks risk at match time
    too, since exposure can have moved since the order was placed."""
    symbol = body.symbol.upper()
    working = session.exec(select(Order).where(
        Order.tenant_id == user.tenant_id, Order.symbol == symbol, Order.status == "new",
    )).all()
    if not working:
        return {"filled": []}

    block_reason = _check_pretrade_audited(symbol, user.email, user.tenant_id, session)
    filled = []
    for o in working:
        if o.limit_price is None or not oms.crosses(o.side, o.limit_price, body.price):
            continue
        if block_reason:
            continue  # leave it working; risk gate blocks the fill, not the order
        o.status = "filled"
        o.fill_price = o.limit_price
        session.add(o)
        _fold_fill_into_position(session, user.tenant_id, symbol, o.side, o.qty, o.limit_price)
        filled.append(o)
    session.commit()
    for o in filled:
        session.refresh(o)
        log_event(session, user.email, "order_matched", f"{o.side} {o.qty} {symbol}",
                  detail=f"limit@{o.limit_price}", tenant_id=user.tenant_id)
    return {"filled": [_order_api(o) for o in filled],
            "blocked_by_risk": bool(block_reason) and any(
                oms.crosses(o.side, o.limit_price, body.price) for o in working if o.limit_price is not None)}


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, user: CurrentUser = Depends(require_trading),
                 session: Session = Depends(get_session)):
    o = session.get(Order, order_id)
    if o is None or o.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="order not found")
    if o.status != "new":
        raise HTTPException(status_code=400, detail=f"order is {o.status}, cannot cancel")
    o.status = "cancelled"
    session.add(o)
    session.commit()
    return _order_api(o)


# ---- positions / portfolio ------------------------------------------------

def _parse_marks(marks: str) -> dict:
    out = {}
    for pair in [p for p in (marks or "").split(",") if ":" in p]:
        sym, price = pair.split(":", 1)
        try:
            out[sym.strip().upper()] = float(price)
        except ValueError:
            pass
    return out


@router.get("/positions")
def positions(marks: str = "", user: CurrentUser = Depends(require_tenant_scope),
              session: Session = Depends(get_session)):
    """Portfolio valuation. Pass ?marks=AAPL:178.1,MSFT:330 to value at current
    prices; without marks, positions are valued at cost."""
    rows = session.exec(select(Position).where(Position.tenant_id == user.tenant_id)).all()
    price_map = _parse_marks(marks)
    positions = [{"symbol": p.symbol, "qty": p.qty, "avg_price": p.avg_price}
                 for p in rows if p.qty != 0]
    summary = pf.portfolio_summary(positions, price_map)
    summary["realized_pnl"] = sum(p.realized_pnl for p in rows)
    return summary


# ---- analytics: greeks / market / forecast -------------------------------

class GreeksBody(BaseModel):
    spot: float
    strike: float
    t_years: float
    vol: float
    rate: float = 0.0
    div_yield: float = 0.0
    kind: str = "call"


@router.post("/greeks")
def compute_greeks(body: GreeksBody, user: CurrentUser = Depends(require_tenant_scope)):
    try:
        return gk.greeks(body.spot, body.strike, body.t_years, body.vol,
                         body.rate, body.div_yield, body.kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class SeriesBody(BaseModel):
    prices: list
    sizes: list | None = None


@router.post("/market")
def market_summary(body: SeriesBody, user: CurrentUser = Depends(require_tenant_scope)):
    return mkt.summarize(body.prices, body.sizes)


class ForecastBody(BaseModel):
    prices: list
    horizon: int = 10


@router.post("/forecast")
def forecast(body: ForecastBody, user: CurrentUser = Depends(require_tenant_scope)):
    return mkt.forecast(body.prices, horizon=body.horizon)


# ---- helpers --------------------------------------------------------------

def _order_api(o: Order) -> dict:
    return {"id": o.id, "symbol": o.symbol, "side": o.side, "qty": o.qty,
            "order_type": o.order_type, "limit_price": o.limit_price,
            "status": o.status, "route": o.route, "fill_price": o.fill_price,
            "created_at": o.created_at.isoformat() if o.created_at else None}
