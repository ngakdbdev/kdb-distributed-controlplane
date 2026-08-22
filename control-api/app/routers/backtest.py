"""
backtest.py (router) - runs app/backtest.py's walk-forward replay against
real HDB history and returns the result. Analysis, not an admin action -
require_tenant_scope (same level as Query/Predictive Signals), not
require_admin.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import backtest as bt
from .auth import CurrentUser, require_tenant_scope

router = APIRouter(prefix="/backtest", tags=["backtest"])


class BacktestBody(BaseModel):
    symbol: str
    start: datetime
    end: datetime
    stop_loss_pct: float = 1.5
    lookback_min: int = 60
    step_min: int = 5


@router.post("/run")
def run(body: BacktestBody, user: CurrentUser = Depends(require_tenant_scope)):
    if body.end <= body.start:
        raise HTTPException(status_code=400, detail="end must be after start")
    if body.stop_loss_pct <= 0:
        raise HTTPException(status_code=400, detail="stop_loss_pct must be positive")
    symbol = body.symbol.upper()
    rows = bt.fetch_historical_trades(symbol, body.start, body.end)
    if not rows:
        raise HTTPException(status_code=404,
                            detail=f"no historical trade prints found for {symbol} in that window "
                                   f"(check the symbol and date range against what's actually in HDB)")
    result = bt.run_backtest(rows, symbol, body.start, body.end,
                             stop_loss_pct=body.stop_loss_pct,
                             lookback_min=body.lookback_min, step_min=body.step_min)
    return result.to_dict()
