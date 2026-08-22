"""
signals.py (router) - the Predictive Signals page's backend: a weighted
composite signal per symbol (signal_composite.py), an aggregated real news
feed (news_feed.py), and portfolio-wide sentiment. Viewing-only, same
tenant-scoped read access as Markets/Portfolio - this never places an order
itself (the bot, app/signal_engine.py, is the only thing that does that,
and only optionally consults this module's composite score - see that
file's own comment on where the two connect).
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from .. import market as mkt
from .. import news_feed
from .. import signal_composite
from .. import signal_engine
from ..db import get_session
from ..models import BotConfig, Position
from .auth import CurrentUser, require_tenant_scope

router = APIRouter(prefix="/signals", tags=["signals"])

_DEFAULT_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]


def _tenant_default_symbols(user: CurrentUser, session: Session) -> list:
    """No ?symbols given - fall back to the tenant's own configured bot
    basket (manual mode) if there is one, else a small illustrative default.
    Auto-mode's basket changes symbol-to-symbol at runtime (it screens the
    live universe), so there's no fixed list to default to there - falls
    through to the same default as an unconfigured bot."""
    cfg = session.exec(select(BotConfig).where(BotConfig.tenant_id == user.tenant_id)).first()
    if cfg and cfg.mode == "manual":
        import json
        syms = json.loads(cfg.symbols_json or "[]")
        if syms:
            return syms
    return _DEFAULT_SYMBOLS


@router.get("/predictive")
def predictive(symbols: Optional[str] = Query(None, description="comma-separated; defaults to your bot's basket"),
              user: CurrentUser = Depends(require_tenant_scope),
              session: Session = Depends(get_session)):
    """Weighted composite signal per symbol - see signal_composite.py for
    the five factors and their default weights. Every factor is computed
    from real data (recent trade prints for momentum/vol/mean-reversion/
    order-flow, real news for sentiment) - a symbol with no data for a
    given factor just has that factor drop out of its blend rather than
    silently scoring it as bearish, see signal_composite's own comment."""
    syms = [s.strip().upper() for s in symbols.split(",")] if symbols else _tenant_default_symbols(user, session)
    syms = syms[:signal_engine.UNIVERSE_SCAN_CAP]

    tape = signal_engine.fetch_trade_tape(syms)
    news = news_feed.fetch_news(symbols=syms)
    news_by_symbol = {s: news_feed.sentiment_for_symbol(s, news_items=news["items"]) for s in syms}

    candidates = []
    for sym in syms:
        rows = tape.get(sym, [])
        prices = [r["price"] for r in rows if r.get("price") is not None]
        if len(prices) < 2:
            continue
        sizes = [r.get("size") for r in rows]
        summary = mkt.summarize(prices, sizes)
        forecast = signal_engine.build_time_forecast(rows)
        sentiment = news_by_symbol.get(sym, {})
        candidates.append({
            "symbol": sym,
            "drift_per_min": forecast.get("drift_per_min") or 0.0,
            "realized_vol_annualized": summary.get("realized_vol_annualized") or 0.0,
            "prices": prices,
            "trade_rows": rows,
            "news_sentiment": sentiment["score"] if sentiment.get("n") else None,
        })

    scored = signal_composite.composite_signal(candidates)
    return {
        "signals": scored,
        "weights": signal_composite.DEFAULT_WEIGHTS,
        "news_sources": news["sources"],
        "symbols_evaluated": len(candidates),
        "symbols_requested": len(syms),
    }


@router.get("/news")
def news(symbols: Optional[str] = Query(None), limit: int = 30,
        user: CurrentUser = Depends(require_tenant_scope)):
    """The raw aggregated news feed - see news_feed.py for sources, the
    keyword-vs-model sentiment distinction, and the honest caveat on region
    tagging being a heuristic, not real geo-data."""
    syms = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    limit = max(1, min(limit, 100))
    return news_feed.fetch_news(symbols=syms, limit=limit)


@router.get("/portfolio-sentiment")
def portfolio_sentiment(user: CurrentUser = Depends(require_tenant_scope),
                        session: Session = Depends(get_session)):
    """Position-value-weighted average news sentiment across your CURRENT
    open positions - "how is the news flow leaning, weighted by how much of
    your book is actually exposed to it", not just an unweighted average
    across whatever you happen to hold. Valued at cost basis (qty *
    avg_price) since this endpoint has no live mark feed of its own - same
    "at cost unless you tell me otherwise" default /trading/positions uses."""
    rows = session.exec(select(Position).where(Position.tenant_id == user.tenant_id)).all()
    held = [p for p in rows if p.qty != 0]
    if not held:
        return {"weighted_sentiment": 0.0, "positions": [], "n_positions": 0}

    symbols = [p.symbol for p in held]
    news = news_feed.fetch_news(symbols=symbols)
    weighted_sum = 0.0
    weight_total = 0.0
    breakdown = []
    for p in held:
        sentiment = news_feed.sentiment_for_symbol(p.symbol, news_items=news["items"])
        market_value = abs(p.qty * p.avg_price)
        weighted_sum += sentiment["score"] * market_value
        weight_total += market_value
        breakdown.append({"symbol": p.symbol, "sentiment": sentiment["score"],
                          "n_articles": sentiment["n"], "market_value": market_value})
    weighted = weighted_sum / weight_total if weight_total else 0.0
    return {"weighted_sentiment": round(weighted, 4), "positions": breakdown, "n_positions": len(held)}
