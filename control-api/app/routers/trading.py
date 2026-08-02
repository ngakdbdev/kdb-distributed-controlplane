"""
trading.py (router) - the subscriber trading terminal backend.

Viewing (market metrics, portfolio, greeks, forecast) is open to any tenant
user. PLACING ORDERS is gated behind the can_trade permission ("if permitted").
Orders run through the paper OMS by default (route='paper'); a real broker route
is a configured seam that refuses (see app/oms.py). Forecasts are illustrative
statistical projections, not advice (see app/market.py).
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import greeks as gk
from .. import market as mkt
from .. import oms
from .. import portfolio as pf
from ..db import get_session, log_event
from ..models import Order, Position, User
from .auth import CurrentUser, get_current_user, require_tenant_scope

router = APIRouter(prefix="/trading", tags=["trading"])

_PAPER = oms.PaperRouter()


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


# ---- permission (so the UI can show/hide the order ticket) ---------------

@router.get("/permission")
def my_permission(user: CurrentUser = Depends(get_current_user),
                  session: Session = Depends(get_session)):
    row = session.get(User, user.user_id)
    can = user.role in ("platform_admin", "tenant_admin") or bool(row and row.can_trade)
    return {"can_trade": can, "live_routing": False, "mode": "paper"}


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


@router.post("/orders")
def place_order(body: OrderBody, user: CurrentUser = Depends(require_trading),
                session: Session = Depends(get_session)):
    if body.side.lower() not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="side must be buy or sell")
    if body.qty <= 0:
        raise HTTPException(status_code=400, detail="qty must be positive")
    try:
        fill = _PAPER.fill(body.side, body.qty, body.order_type, body.ref_price, body.limit_price)
    except oms.OrderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    order = Order(tenant_id=user.tenant_id, user_email=user.email, symbol=body.symbol.upper(),
                  side=body.side.lower(), qty=body.qty, order_type=body.order_type,
                  limit_price=body.limit_price, status="filled", route=fill.route,
                  fill_price=fill.price)
    session.add(order)

    # fold the fill into the tenant's position
    pos = session.exec(select(Position).where(Position.tenant_id == user.tenant_id,
                                              Position.symbol == order.symbol)).first()
    if pos is None:
        pos = Position(tenant_id=user.tenant_id, symbol=order.symbol, qty=0.0, avg_price=0.0)
    new_qty, new_avg, realized = oms.apply_to_position(pos.qty, pos.avg_price,
                                                       body.side, body.qty, fill.price)
    pos.qty, pos.avg_price = new_qty, new_avg
    pos.realized_pnl += realized
    pos.updated_at = datetime.utcnow()
    session.add(pos)
    session.commit()
    session.refresh(order)
    log_event(session, user.email, "order_placed",
              f"{body.side} {body.qty} {order.symbol}",
              detail=f"{fill.route}@{fill.price}", tenant_id=user.tenant_id)
    return _order_api(order)


@router.get("/orders")
def list_orders(user: CurrentUser = Depends(require_tenant_scope),
                session: Session = Depends(get_session)):
    rows = session.exec(select(Order).where(Order.tenant_id == user.tenant_id)
                        .order_by(Order.created_at.desc())).all()
    return [_order_api(o) for o in rows]


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
