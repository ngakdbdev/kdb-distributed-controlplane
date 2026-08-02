"""
portfolio.py - portfolio valuation and risk metrics from positions + prices.
Pure and tested. Handles long and short positions (negative qty).
"""
from __future__ import annotations


def position_metrics(positions: list, prices: dict) -> list:
    """positions: [{symbol, qty, avg_price}]; prices: {symbol: last}.
    Returns per-position valuation + P&L."""
    out = []
    for p in positions:
        sym = p["symbol"]
        qty = float(p.get("qty", 0))
        avg = float(p.get("avg_price", 0))
        last = float(prices.get(sym, avg))
        mv = qty * last
        cost = qty * avg
        pnl = mv - cost
        out.append({
            "symbol": sym, "qty": qty, "avg_price": avg, "last": last,
            "market_value": mv, "cost_basis": cost, "unrealized_pnl": pnl,
            "pnl_pct": (pnl / cost * 100.0) if cost else 0.0,
            "side": "long" if qty >= 0 else "short",
        })
    return out


def portfolio_summary(positions: list, prices: dict) -> dict:
    """Aggregate metrics across positions."""
    legs = position_metrics(positions, prices)
    gross = sum(abs(l["market_value"]) for l in legs)
    net = sum(l["market_value"] for l in legs)
    cost = sum(l["cost_basis"] for l in legs)
    pnl = sum(l["unrealized_pnl"] for l in legs)
    # weights of gross exposure + concentration (largest single-name share)
    for l in legs:
        l["weight_pct"] = (abs(l["market_value"]) / gross * 100.0) if gross else 0.0
    concentration = max((l["weight_pct"] for l in legs), default=0.0)
    longs = sum(l["market_value"] for l in legs if l["qty"] >= 0)
    shorts = sum(l["market_value"] for l in legs if l["qty"] < 0)
    return {
        "positions": legs,
        "gross_exposure": gross,
        "net_exposure": net,
        "long_exposure": longs,
        "short_exposure": shorts,
        "cost_basis": cost,
        "unrealized_pnl": pnl,
        "pnl_pct": (pnl / cost * 100.0) if cost else 0.0,
        "concentration_pct": concentration,
        "position_count": len(legs),
    }
