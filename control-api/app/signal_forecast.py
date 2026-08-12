"""
signal_forecast.py - calendar-horizon forecast (next 10m/15m/30m/1h), built
from real trade timestamps bucketized into 1-minute bars.

A direct Python port of web-ui/src/lib/timeForecast.js's buildTimeForecast.
That file's own docstring explains why this differs from market.py's
forecast(): that one projects over N raw ticks with no time semantics at all
- "horizon: 10" means "the next 10 ticks," which could be a fraction of a
second or several minutes away depending on feed rate. This bucketizes real
trade prints into genuine 1-minute bars first (each bar's last print as its
close), so a horizon of 10 actually means 10 real minutes. Kept as a
separate module (not folded into market.py) for the same reason the JS
version is separate from the old forecast card: different time semantics,
not a drop-in replacement.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

HORIZONS_MIN = (10, 15, 30, 60)
BUCKET_MS = 60_000
TREND_THRESHOLD = 0.0002


def _parse_time_ms(t) -> Optional[float]:
    if t is None:
        return None
    if isinstance(t, (int, float)):
        return float(t)
    if isinstance(t, datetime):
        dt = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000.0
    try:
        s = str(t)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000.0
    except (ValueError, TypeError):
        return None


def build_time_forecast(rows, horizons=HORIZONS_MIN, z: float = 1.645) -> dict:
    """rows: iterable of {"time": ..., "price": ...} (time as ISO string,
    epoch-ms number, or datetime; extra keys ignored). Mirrors
    buildTimeForecast()'s return shape (snake_case here vs camelCase there)."""
    clean = []
    for r in rows or []:
        t_ms = _parse_time_ms(r.get("time"))
        price = r.get("price")
        if t_ms is None or price is None:
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(t_ms) and math.isfinite(price)):
            continue
        clean.append((t_ms, price))
    clean.sort(key=lambda x: x[0])

    if len(clean) < 3:
        return {
            "points": [], "method": "insufficient-data", "trend": "flat", "sampled_minutes": 0,
            "sufficient_data": False, "last": clean[-1][1] if clean else None,
            "drift_per_min": None, "vol_per_min": None,
            "disclaimer": "Not enough recent trade prints to forecast yet.",
        }

    bars = []
    bucket_start = math.floor(clean[0][0] / BUCKET_MS) * BUCKET_MS
    cur = None
    for t_ms, price in clean:
        while t_ms >= bucket_start + BUCKET_MS:
            bucket_start += BUCKET_MS
            cur = None
        if cur is None:
            cur = {"t": bucket_start, "close": price}
            bars.append(cur)
        cur["close"] = price

    closes = [b["close"] for b in bars]
    sampled_minutes = len(bars)
    last = closes[-1]

    if len(closes) < 3:
        return {
            "points": [], "method": "insufficient-data", "trend": "flat", "sampled_minutes": sampled_minutes,
            "sufficient_data": False, "last": last, "drift_per_min": None, "vol_per_min": None,
            "disclaimer": "Recent prints span too little real time (under a few minutes) to project "
                          "a calendar-time forecast yet.",
        }

    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    n = len(rets)
    drift_per_min = (sum(rets) / n) if n else 0.0
    variance = (sum((x - drift_per_min) ** 2 for x in rets) / (n - 1)) if n > 1 else 0.0
    vol_per_min = math.sqrt(variance)
    trend = "up" if drift_per_min > TREND_THRESHOLD else "down" if drift_per_min < -TREND_THRESHOLD else "flat"

    points = []
    for h in horizons:
        expected = last * math.exp(drift_per_min * h)
        spread = z * vol_per_min * math.sqrt(h)
        points.append({
            "horizon_min": h, "label": f"+{h}m" if h < 60 else f"+{h // 60}h",
            "expected": expected, "lower": last * math.exp(drift_per_min * h - spread),
            "upper": last * math.exp(drift_per_min * h + spread),
            "delta_pct": ((expected - last) / last) * 100.0 if last else 0.0,
        })

    longest_horizon = max(horizons)
    sufficient_data = sampled_minutes >= longest_horizon / 3

    return {
        "points": points, "method": "drift + volatility over 1-minute bars", "trend": trend, "last": last,
        "drift_per_min": drift_per_min, "vol_per_min": vol_per_min,
        "sampled_minutes": sampled_minutes, "sufficient_data": sufficient_data,
        "disclaimer": (
            "Statistical projection from recent price drift and volatility over 1-minute bars - "
            "not advice, and the confidence band widens fast the further out you look."
        ) if sufficient_data else (
            f"Based on only ~{sampled_minutes} minute(s) of sampled trades - anything past that is "
            "extrapolated well beyond the sampled window, treat longer horizons here as low-confidence."
        ),
    }
