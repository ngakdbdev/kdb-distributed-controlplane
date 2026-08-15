"""
signal_composite.py - a weighted, multi-factor predictive signal, blending
several standard quantitative-trading techniques into one score per symbol.
Backs the Predictive Signals page and (optionally, additively - see
signal_engine.py) the bot's auto-mode symbol screening.

WHAT THIS IS, HONESTLY: real, standard systematic-trading factors, computed
from data this pipeline actually has (recent trade prints) or actually
integrates (news_feed.py) - not a black box, not backtested/optimized, and
explicitly NOT "high-frequency trading" in the industry sense of the term.
That distinction matters enough to spell out before the factor list:

  * True HFT (market making, latency arbitrage, sub-millisecond order-book
    signals) depends on colocated execution and direct exchange feeds with
    single-digit-microsecond latency. This system talks to kdb+ over a
    network and places orders through REST broker APIs (Alpaca, IBKR) that
    round-trip in tens to hundreds of milliseconds even in the best case -
    architecturally incapable of HFT regardless of the signal math used on
    top, and nothing here should be described as HFT to a customer.
  * What IS realistic, and what this module actually does, is SYSTEMATIC /
    QUANTITATIVE signal-driven trading - the same category of technique
    real desks use at second-to-minute-to-day holding periods, just without
    the latency arms race. That's a genuinely useful, honestly-described
    category on its own.

The five factors, each a real, named technique:

  1. TIME-SERIES MOMENTUM (trend-following). "Buy what's been going up,
     sell what's been going down" - Moskowitz/Ooi/Pedersen's "Time Series
     Momentum" (2012) is the canonical reference. Here: signal_engine.py's
     rank_by_momentum() drift-per-minute, from real recent trade prints.

  2. VOLATILITY-SCALED MOMENTUM (risk-adjusted trend). Raw drift is scaled
     by realized volatility (a Sharpe-ratio-style transform: return / risk)
     rather than taken at face value - the standard "risk parity" style
     correction so a volatile, noisy drift doesn't get equal weight to a
     calm, steady one of the same raw magnitude. Uses market.py's
     realized_vol_annualized (already computed, real, log-return stdev).

  3. MEAN REVERSION (short-horizon z-score). The Ornstein-Uhlenbeck
     intuition behind stat-arb/pairs trading: price far from its own recent
     mean tends to pull back. This is the SINGLE-SYMBOL simplification (a
     z-score of last price vs a short moving average/stdev) - real pairs
     trading needs a cointegration test (Engle-Granger/Johansen) across a
     BASKET of symbols, which this module does not implement; that's a
     real, larger piece of infrastructure (finding cointegrated pairs,
     tracking a spread, not a single symbol's own price history) left as a
     documented gap, not silently approximated as something it isn't.

  4. ORDER-FLOW IMBALANCE (market microstructure). A real signal from
     market-microstructure theory: classify each trade as buyer- or
     seller-initiated and compute (buy volume - sell volume) / total
     volume over a recent window - sustained one-sided flow often
     precedes/confirms a move. Classified here via the TICK RULE (price
     up from the previous trade = buy-initiated, down = sell-initiated,
     unchanged = keep the previous classification) - the standard
     simplification when only trade prints are available, not full
     Lee-Ready (which needs the bid/ask midpoint; this pipeline's RDB
     schema has no quote data, only prints - see Portfolio/Markets pages'
     own "illustrative NBBO, not a real book" disclaimers for the same
     underlying data limitation).

  5. NEWS SENTIMENT (alternative data). Recency-decayed average sentiment
     from news_feed.py - real headlines, either a real NLP model's score
     (Alpha Vantage, when configured) or this codebase's own keyword
     heuristic (Finnhub, always available with the existing key) - see
     that module for exactly which. Increasingly standard as an
     "alternative data" input alongside price-based factors, not a
     replacement for them.

DEFAULT WEIGHTS below are a reasonable, documented STARTING BLEND - equal-ish
with a momentum tilt (matching this pipeline's existing bot strategy, which
is momentum-based) - NOT the output of any backtest or optimization run
against this system's own data. Treat them as a starting point to tune
against your own results, exactly the same honesty this codebase already
applies to its statistical forecast (market.py: "not a prediction, not
investment advice").
"""
from __future__ import annotations

import math
from typing import Optional

DEFAULT_WEIGHTS = {
    "momentum": 0.30,
    "vol_scaled_momentum": 0.25,
    "mean_reversion": 0.15,
    "order_flow_imbalance": 0.15,
    "news_sentiment": 0.15,
}


def _zscore(values: list) -> list:
    """Cross-sectional standardization (z-score across the CURRENT candidate
    set, not a fixed historical scale) - this is what makes momentum/vol-
    scaled-momentum comparable across symbols with very different raw price/
    volatility levels, the same "rank within today's universe" idea
    signal_engine.py's existing sort-by-drift already does informally; this
    just makes it a proper standardized score instead of a raw sort key."""
    if not values:
        return []
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n if n > 1 else 0.0
    sd = math.sqrt(var)
    if sd == 0:
        return [0.0] * n
    return [(v - mean) / sd for v in values]


def mean_reversion_zscore(prices: list, lookback: int = 20) -> Optional[float]:
    """z-score of the LAST price vs the mean/stdev of the preceding
    `lookback` prints (excluding the last one itself). Positive = price is
    stretched above its recent mean (a reversion signal would lean
    negative/sell); negative = stretched below (lean positive/buy) - so the
    FACTOR value returned here is the negated z-score, i.e. already
    oriented "higher = more bullish", matching every other factor's sign
    convention, not the raw statistical z-score."""
    prices = [float(p) for p in prices if p is not None]
    if len(prices) < lookback + 1:
        return None
    window = prices[-(lookback + 1):-1]
    last = prices[-1]
    mean = sum(window) / len(window)
    var = sum((p - mean) ** 2 for p in window) / len(window)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0
    z = (last - mean) / sd
    return -z  # oriented bullish-positive, see docstring


def order_flow_imbalance(rows: list) -> Optional[float]:
    """Tick-rule-classified order-flow imbalance in [-1, 1] - see module
    docstring for the technique and its "only trade prints, no quotes"
    caveat. `rows`: [{"price", "size"}] in chronological order (oldest
    first)."""
    clean = [(float(r["price"]), float(r.get("size") or 0)) for r in rows
            if r.get("price") is not None]
    if len(clean) < 2:
        return None
    buy_vol = sell_vol = 0.0
    last_direction = 1  # default to buy-classified if the series opens flat
    prev_price = clean[0][0]
    for price, size in clean[1:]:
        if price > prev_price:
            last_direction = 1
        elif price < prev_price:
            last_direction = -1
        # unchanged -> keep last_direction (the tick rule's own convention)
        if last_direction > 0:
            buy_vol += size
        else:
            sell_vol += size
        prev_price = price
    total = buy_vol + sell_vol
    if total == 0:
        return 0.0
    return (buy_vol - sell_vol) / total


def composite_signal(candidates: list, weights: Optional[dict] = None) -> list:
    """candidates: [{"symbol", "drift_per_min", "realized_vol_annualized",
    "prices" (recent, chronological), "trade_rows" ([{"price","size"}]),
    "news_sentiment" (score or None)}, ...] - callers (routers/signals.py)
    assemble this from signal_engine.rank_by_momentum, market.summarize,
    and news_feed.sentiment_for_symbol; this function is pure and doesn't
    fetch anything itself, so it's fully unit-testable with canned inputs.

    Returns each candidate's composite score PLUS the per-factor breakdown
    (raw and weighted) - the Predictive Signals page shows this breakdown
    explicitly ("weighted signals", not a black-box number) rather than
    only the final blend."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    total_w = sum(w.values()) or 1.0

    momentum_raw = _zscore([c.get("drift_per_min") or 0.0 for c in candidates])
    vol_scaled_raw = []
    for c in candidates:
        drift = c.get("drift_per_min") or 0.0
        vol = c.get("realized_vol_annualized") or 0.0
        vol_scaled_raw.append(drift / vol if vol > 1e-9 else 0.0)
    vol_scaled_raw = _zscore(vol_scaled_raw)

    out = []
    for i, c in enumerate(candidates):
        mr = mean_reversion_zscore(c.get("prices") or [])
        ofi = order_flow_imbalance(c.get("trade_rows") or [])
        news = c.get("news_sentiment")

        factors = {
            "momentum": momentum_raw[i],
            "vol_scaled_momentum": vol_scaled_raw[i],
            "mean_reversion": mr if mr is not None else 0.0,
            "order_flow_imbalance": ofi if ofi is not None else 0.0,
            "news_sentiment": news if news is not None else 0.0,
        }
        available = {
            "momentum": True, "vol_scaled_momentum": True,
            "mean_reversion": mr is not None,
            "order_flow_imbalance": ofi is not None,
            "news_sentiment": news is not None,
        }
        # re-normalize weights over only the factors that actually had data
        # (a symbol with no news yet shouldn't have that factor silently
        # count as a bearish 0 against it at full weight - it drops out of
        # the blend instead, redistributing its weight proportionally)
        usable_w_sum = sum(w[k] for k, ok in available.items() if ok) or 1.0
        weighted = {k: factors[k] * w[k] / usable_w_sum for k in factors if available[k]}
        composite = sum(weighted.values())

        out.append({
            "symbol": c["symbol"],
            "composite_score": round(composite, 4),
            "factors": {k: {"value": round(factors[k], 4),
                            "weight": round(w[k] / total_w, 3),
                            "available": available[k]}
                       for k in factors},
        })
    out.sort(key=lambda r: r["composite_score"], reverse=True)
    return out
