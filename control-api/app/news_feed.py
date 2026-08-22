"""
news_feed.py - real financial news, normalized to one shape, for the
Predictive Signals page and signal_composite.py's sentiment factor.

Two sources, both optional independently (this degrades gracefully, never
fakes an article):

  * Finnhub (FINNHUB_API_KEY - the same key the finnhub-feed market-data
    provider already uses) - general market news (/news?category=general)
    and per-symbol news (/company-news). No sentiment score of its own;
    see _keyword_sentiment below for how this module derives one.
  * Alpha Vantage NEWS_SENTIMENT (ALPHAVANTAGE_API_KEY, optional, separate
    from the finnhub key) - REAL per-article sentiment scores and
    per-ticker relevance, when configured. Alpha Vantage's free tier is
    heavily rate-limited (historically ~25 requests/day) - this is treated
    as a low-frequency supplementary source, never the only one an empty
    news page would depend on.

HONEST CAVEAT ON "GEOGRAPHIES": neither source provides real, structured
geographic tagging on the free tier. `_infer_region` below is a best-effort
keyword/source-domain heuristic (matches on well-known institutions/indices
per region) - useful for a rough "where is this about" grouping, NOT an
authoritative classification. Every article carries `region_confidence:
"heuristic"` so nothing downstream mistakes it for real geo-tagging. A
production build wanting real geographic coverage would want a source like
GDELT (the Global Database of Events, Language, and Tone) instead.

Cached with a short TTL (module-level, per-process) so the Predictive
Signals page doesn't hammer either API on every poll - see _CACHE_TTL_SEC.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("news_feed")

_CACHE_TTL_SEC = 180  # 3 min - real news doesn't need second-level freshness,
                      # and this keeps well inside both vendors' free-tier limits
_cache: dict = {}     # cache key -> (fetched_at_monotonic, items)

# ---------------------------------------------------------------------------
# best-effort region inference - see module docstring's honest caveat
# ---------------------------------------------------------------------------
_REGION_KEYWORDS = {
    "US": ("federal reserve", "fed ", "wall street", "s&p 500", "nasdaq", "dow jones",
          "treasury", "sec ", "white house", "nyse"),
    "Europe": ("ecb", "eurozone", "european union", "ftse", "dax", "cac 40",
              "bank of england", "brexit", "euro area"),
    "Asia-Pacific": ("nikkei", "shanghai", "hang seng", "bank of japan", "pboc",
                     "reserve bank of india", "asx", "kospi", "boj", "china's"),
    "UK": ("bank of england", "ftse 100", "downing street", "westminster"),
}


def _infer_region(text: str) -> str:
    low = (text or "").lower()
    for region, keywords in _REGION_KEYWORDS.items():
        if any(k in low for k in keywords):
            return region
    return "Global/Unclassified"


# crude polarity lexicon for Finnhub articles, which carry no sentiment
# score of their own - counts hits from a small, hand-picked finance-domain
# word list. This is NOT NLP, it's a word-count heuristic - explicitly
# weaker than Alpha Vantage's real model-based score, and always labeled
# "keyword" (vs Alpha Vantage's "model") in sentiment_source so
# signal_composite.py (and the UI) can weight/display them differently.
_POS_WORDS = ("surge", "rally", "beat", "beats", "upgrade", "growth", "record high",
             "outperform", "gains", "jumps", "soars", "bullish", "profit")
_NEG_WORDS = ("plunge", "slump", "miss", "misses", "downgrade", "recession", "record low",
             "underperform", "losses", "falls", "sinks", "bearish", "layoffs", "lawsuit")


def _keyword_sentiment(text: str) -> float:
    low = (text or "").lower()
    pos = sum(1 for w in _POS_WORDS if w in low)
    neg = sum(1 for w in _NEG_WORDS if w in low)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 3)  # in [-1, 1]


def _http_get_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"User-Agent": "kdb-control-plane"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _cached(key: str, fetch):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL_SEC:
        return hit[1]
    items = fetch()
    _cache[key] = (now, items)
    return items


def _finnhub_general(api_key: str, limit: int) -> list:
    url = f"https://finnhub.io/api/v1/news?category=general&token={api_key}"
    raw = _http_get_json(url)
    out = []
    for a in (raw or [])[:limit]:
        headline = a.get("headline", "")
        summary = a.get("summary", "")
        text = f"{headline} {summary}"
        out.append({
            "headline": headline, "summary": summary,
            "source": a.get("source", "finnhub"), "url": a.get("url", ""),
            "published_at": _ts_to_iso(a.get("datetime")),
            "symbols": [],
            "sentiment_score": _keyword_sentiment(text),
            "sentiment_source": "keyword",
            "region": _infer_region(text), "region_confidence": "heuristic",
        })
    return out


def _finnhub_company(api_key: str, symbol: str, limit: int) -> list:
    today = datetime.now(timezone.utc).date()
    frm = (today.toordinal() - 14)  # ~2 weeks back, plenty for a recency-decayed sentiment factor
    from datetime import date
    url = (f"https://finnhub.io/api/v1/company-news?symbol={urllib.parse.quote(symbol)}"
          f"&from={date.fromordinal(frm).isoformat()}&to={today.isoformat()}&token={api_key}")
    raw = _http_get_json(url)
    out = []
    for a in (raw or [])[:limit]:
        headline = a.get("headline", "")
        summary = a.get("summary", "")
        text = f"{headline} {summary}"
        out.append({
            "headline": headline, "summary": summary,
            "source": a.get("source", "finnhub"), "url": a.get("url", ""),
            "published_at": _ts_to_iso(a.get("datetime")),
            "symbols": [symbol],
            "sentiment_score": _keyword_sentiment(text),
            "sentiment_source": "keyword",
            "region": _infer_region(text), "region_confidence": "heuristic",
        })
    return out


def _alphavantage_sentiment(api_key: str, symbols: Optional[list], limit: int) -> list:
    params = {"function": "NEWS_SENTIMENT", "apikey": api_key, "limit": str(limit)}
    if symbols:
        params["tickers"] = ",".join(symbols)
    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode(params)
    raw = _http_get_json(url)
    feed = raw.get("feed") if isinstance(raw, dict) else None
    out = []
    for a in (feed or [])[:limit]:
        text = f"{a.get('title', '')} {a.get('summary', '')}"
        ticker_sent = a.get("ticker_sentiment") or []
        syms = [t.get("ticker") for t in ticker_sent if t.get("ticker")]
        out.append({
            "headline": a.get("title", ""), "summary": a.get("summary", ""),
            "source": a.get("source", "alphavantage"), "url": a.get("url", ""),
            "published_at": _av_ts_to_iso(a.get("time_published")),
            "symbols": syms,
            "sentiment_score": round(float(a.get("overall_sentiment_score", 0.0)), 3),
            "sentiment_source": "model",  # Alpha Vantage's own NLP model, not our keyword heuristic
            "region": _infer_region(text), "region_confidence": "heuristic",
        })
    return out


def _ts_to_iso(unix_seconds) -> Optional[str]:
    try:
        return datetime.fromtimestamp(float(unix_seconds), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _av_ts_to_iso(raw) -> Optional[str]:
    # Alpha Vantage's own compact format: "20260810T143000"
    if not raw or not re.match(r"^\d{8}T\d{6}$", str(raw)):
        return None
    try:
        dt = datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return None


def fetch_news(symbols: Optional[list] = None, limit: int = 30) -> dict:
    """The Predictive Signals page's news feed: general market news plus,
    when symbols are given, per-symbol news - merged, newest first. Returns
    {"items": [...], "sources": {"finnhub": bool, "alphavantage": bool}} so
    the UI can show exactly which sources actually contributed (never claim
    coverage from a source that wasn't configured)."""
    finnhub_key = os.environ.get("FINNHUB_API_KEY", "")
    av_key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    items = []
    sources_used = {"finnhub": False, "alphavantage": False}

    if finnhub_key:
        try:
            key = f"finnhub-general:{limit}"
            items.extend(_cached(key, lambda: _finnhub_general(finnhub_key, limit)))
            sources_used["finnhub"] = True
            if symbols:
                for sym in symbols[:5]:  # cap per-symbol lookups - free-tier rate limits
                    key = f"finnhub-company:{sym}:{limit}"
                    items.extend(_cached(key, lambda s=sym: _finnhub_company(finnhub_key, s, limit)))
        except (urllib.error.URLError, ValueError, KeyError) as exc:
            log.warning("finnhub news fetch failed: %s", exc)

    if av_key:
        try:
            key = f"alphavantage:{','.join(symbols or [])}:{limit}"
            av_items = _cached(key, lambda: _alphavantage_sentiment(av_key, symbols, limit))
            items.extend(av_items)
            sources_used["alphavantage"] = True
        except (urllib.error.URLError, ValueError, KeyError) as exc:
            log.warning("alphavantage news fetch failed: %s", exc)

    items.sort(key=lambda a: a.get("published_at") or "", reverse=True)
    return {"items": items[:limit], "sources": sources_used}


def sentiment_for_symbol(symbol: str, news_items: Optional[list] = None) -> dict:
    """Recency-weighted average sentiment for one symbol - the factor
    signal_composite.py's news weight consumes. Exponential time-decay
    (half-life ~24h, a standard, simple recency-weighting choice for daily-
    ish news flow - not tuned/backtested, a documented starting point like
    every other weight in this module) so a week-old article barely counts
    against this morning's headline. Returns 0.0 (neutral) with n=0 if no
    relevant articles are found - never fabricates a score."""
    items = news_items if news_items is not None else fetch_news(symbols=[symbol])["items"]
    relevant = [a for a in items if symbol in (a.get("symbols") or [])]
    if not relevant:
        return {"symbol": symbol, "score": 0.0, "n": 0}
    now = datetime.now(timezone.utc)
    HALF_LIFE_HOURS = 24.0
    weighted_sum = 0.0
    weight_total = 0.0
    for a in relevant:
        ts = a.get("published_at")
        age_hours = 12.0  # unknown timestamp -> assume half a day old, a neutral-ish default
        if ts:
            try:
                published = datetime.fromisoformat(ts)
                age_hours = max(0.0, (now - published).total_seconds() / 3600.0)
            except ValueError:
                pass
        weight = 0.5 ** (age_hours / HALF_LIFE_HOURS)
        weighted_sum += a.get("sentiment_score", 0.0) * weight
        weight_total += weight
    score = weighted_sum / weight_total if weight_total else 0.0
    return {"symbol": symbol, "score": round(score, 3), "n": len(relevant)}
