"""
greeks.py - Black-Scholes-Merton option pricing and Greeks. Pure math, fully
tested. Given spot, strike, time-to-expiry (years), volatility, risk-free rate
and (optionally) a continuous dividend yield, returns price and the standard
Greeks for a European call or put.

Conventions in the returned dict:
  price      - option premium
  delta      - dPrice/dSpot
  gamma      - d2Price/dSpot^2
  vega       - per 1.00 change in vol; vega_per_pct is per 1 percentage point
  theta      - per year; theta_per_day is per calendar day
  rho        - per 1.00 change in rate; rho_per_pct is per 1 percentage point
"""
from __future__ import annotations

import math

SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _d1_d2(S, K, t, vol, r, q):
    vt = vol * math.sqrt(t)
    d1 = (math.log(S / K) + (r - q + 0.5 * vol * vol) * t) / vt
    return d1, d1 - vt


def greeks(spot: float, strike: float, t_years: float, vol: float,
           rate: float = 0.0, div_yield: float = 0.0, kind: str = "call") -> dict:
    """Price + Greeks for a European option. kind in {'call','put'}."""
    kind = kind.lower()
    if kind not in ("call", "put"):
        raise ValueError("kind must be 'call' or 'put'")
    if spot <= 0 or strike <= 0 or vol <= 0 or t_years <= 0:
        raise ValueError("spot, strike, vol and t_years must be positive")

    d1, d2 = _d1_d2(spot, strike, t_years, vol, rate, div_yield)
    disc_r = math.exp(-rate * t_years)
    disc_q = math.exp(-div_yield * t_years)
    nd1, nd2 = _norm_cdf(d1), _norm_cdf(d2)
    pdf_d1 = _norm_pdf(d1)

    gamma = disc_q * pdf_d1 / (spot * vol * math.sqrt(t_years))
    vega = spot * disc_q * pdf_d1 * math.sqrt(t_years)

    if kind == "call":
        price = spot * disc_q * nd1 - strike * disc_r * nd2
        delta = disc_q * nd1
        theta = (-(spot * disc_q * pdf_d1 * vol) / (2 * math.sqrt(t_years))
                 - rate * strike * disc_r * nd2 + div_yield * spot * disc_q * nd1)
        rho = strike * t_years * disc_r * nd2
    else:
        price = strike * disc_r * _norm_cdf(-d2) - spot * disc_q * _norm_cdf(-d1)
        delta = -disc_q * _norm_cdf(-d1)
        theta = (-(spot * disc_q * pdf_d1 * vol) / (2 * math.sqrt(t_years))
                 + rate * strike * disc_r * _norm_cdf(-d2) - div_yield * spot * disc_q * _norm_cdf(-d1))
        rho = -strike * t_years * disc_r * _norm_cdf(-d2)

    return {
        "kind": kind, "price": price, "delta": delta, "gamma": gamma,
        "vega": vega, "vega_per_pct": vega / 100.0,
        "theta": theta, "theta_per_day": theta / 365.0,
        "rho": rho, "rho_per_pct": rho / 100.0,
        "d1": d1, "d2": d2,
    }


def implied_vol(price: float, spot: float, strike: float, t_years: float,
                rate: float = 0.0, div_yield: float = 0.0, kind: str = "call",
                tol: float = 1e-6, max_iter: int = 100) -> float | None:
    """Back out volatility from a market premium via bisection. None if it
    doesn't bracket a solution in a sane vol range."""
    lo, hi = 1e-4, 5.0
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        p = greeks(spot, strike, t_years, mid, rate, div_yield, kind)["price"]
        if abs(p - price) < tol:
            return mid
        if p > price:
            hi = mid
        else:
            lo = mid
    return mid if abs(hi - lo) < 1e-3 else None
