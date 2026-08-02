"""
market.py - market-movement summaries and an ILLUSTRATIVE statistical forecast.
Pure and tested.

IMPORTANT on the forecast: this is a transparent statistical projection - a
drift estimate from recent log-returns plus volatility-scaled confidence bands.
It is NOT a prediction you should trade on, NOT investment advice, and markets
are not reliably forecastable by models like this. Every forecast payload
carries a `disclaimer`, and the UI shows it prominently.
"""
from __future__ import annotations

import math

TRADING_DAYS = 252
DISCLAIMER = ("Illustrative statistical projection (drift + volatility bands) from recent prices. "
              "Not a prediction, not investment advice - do not trade on this.")


def summarize(prices: list, sizes: list | None = None) -> dict:
    """Market-movement metrics from a recent price (and optional size) series."""
    prices = [float(p) for p in prices if p is not None]
    if not prices:
        return {"count": 0}
    sizes = [float(s) for s in (sizes or [])] if sizes else []
    first, last = prices[0], prices[-1]
    change = last - first
    vwap = (sum(p * s for p, s in zip(prices, sizes)) / sum(sizes)
            if sizes and sum(sizes) else None)
    rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]
    vol = _stdev(rets)
    return {
        "count": len(prices), "last": last, "open": first,
        "change": change, "change_pct": (change / first * 100.0) if first else 0.0,
        "high": max(prices), "low": min(prices),
        "vwap": vwap, "volume": sum(sizes) if sizes else None,
        "realized_vol_annualized": vol * math.sqrt(TRADING_DAYS) if vol else 0.0,
    }


def forecast(prices: list, horizon: int = 10, z: float = 1.645) -> dict:
    """Project `horizon` steps ahead with drift + volatility bands (z ~ 90% by
    default). Transparent and heavily caveated - see module docstring."""
    prices = [float(p) for p in prices if p is not None]
    if len(prices) < 3:
        return {"points": [], "method": "insufficient-data", "disclaimer": DISCLAIMER}
    rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]
    drift = sum(rets) / len(rets)
    sigma = _stdev(rets)
    last = prices[-1]
    points = []
    for h in range(1, max(1, horizon) + 1):
        expected = last * math.exp(drift * h)
        spread = z * sigma * math.sqrt(h)
        points.append({
            "step": h,
            "expected": expected,
            "lower": last * math.exp(drift * h - spread),
            "upper": last * math.exp(drift * h + spread),
        })
    return {
        "points": points,
        "method": "log-return drift + volatility bands",
        "drift_per_step": drift,
        "vol_per_step": sigma,
        "trend": "up" if drift > 0 else "down" if drift < 0 else "flat",
        "disclaimer": DISCLAIMER,
    }


def _stdev(xs: list) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
