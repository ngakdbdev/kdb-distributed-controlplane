"""Tests for news_feed.py - region heuristic, keyword sentiment, timestamp
parsing, source aggregation/graceful-degradation, and recency-decayed
per-symbol sentiment. Mocked HTTP throughout - no real network calls."""
import pytest

from app import news_feed


@pytest.fixture(autouse=True)
def clean_env_and_cache(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    news_feed._cache.clear()
    yield
    news_feed._cache.clear()


# ---- region inference (explicitly a heuristic - see module docstring) ----

def test_infer_region_us_keywords():
    assert news_feed._infer_region("The Federal Reserve raised rates today") == "US"


def test_infer_region_europe_keywords():
    assert news_feed._infer_region("The ECB held its policy meeting in Frankfurt") == "Europe"


def test_infer_region_asia_pacific_keywords():
    assert news_feed._infer_region("Nikkei climbs as Bank of Japan holds steady") == "Asia-Pacific"


def test_infer_region_defaults_to_unclassified():
    assert news_feed._infer_region("A company announced a new product today") == "Global/Unclassified"


# ---- keyword sentiment (Finnhub's own fallback, no NLP) -------------------

def test_keyword_sentiment_positive():
    assert news_feed._keyword_sentiment("Stock surges on record high profit and strong growth") > 0


def test_keyword_sentiment_negative():
    assert news_feed._keyword_sentiment("Shares plunge on recession fears and layoffs") < 0


def test_keyword_sentiment_neutral_with_no_signal_words():
    assert news_feed._keyword_sentiment("The company held its annual meeting") == 0.0


def test_keyword_sentiment_mixed_cancels_toward_zero():
    # one positive, one negative word -> exactly cancels. ("beats" is
    # deliberately avoided here - "beat" is also in _POS_WORDS and is a
    # substring of "beats", which would double-count a single mention.)
    assert news_feed._keyword_sentiment("Growth is strong but layoffs planned") == 0.0


# ---- timestamp parsing ------------------------------------------------------

def test_ts_to_iso_parses_unix_seconds():
    assert news_feed._ts_to_iso(1700000000) is not None


def test_ts_to_iso_none_on_garbage():
    assert news_feed._ts_to_iso("not-a-number") is None
    assert news_feed._ts_to_iso(None) is None


def test_av_ts_to_iso_parses_alphavantage_compact_format():
    iso = news_feed._av_ts_to_iso("20260810T143000")
    assert iso is not None and iso.startswith("2026-08-10T14:30:00")


def test_av_ts_to_iso_none_on_wrong_shape():
    assert news_feed._av_ts_to_iso("2026-08-10") is None
    assert news_feed._av_ts_to_iso(None) is None


# ---- fetch_news: source aggregation + graceful degradation ---------------

def test_fetch_news_no_keys_configured_returns_empty_and_reports_no_sources():
    result = news_feed.fetch_news(symbols=["AAPL"])
    assert result["items"] == []
    assert result["sources"] == {"finnhub": False, "alphavantage": False}


def test_fetch_news_finnhub_only(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "key")

    def fake_get(url, timeout=10.0):
        if "company-news" in url:
            return [{"headline": "AAPL surges on strong growth", "summary": "", "source": "Reuters",
                     "url": "http://x", "datetime": 1700000000}]
        return [{"headline": "Fed holds rates steady", "summary": "", "source": "Reuters",
                "url": "http://y", "datetime": 1700000001}]

    monkeypatch.setattr(news_feed, "_http_get_json", fake_get)
    result = news_feed.fetch_news(symbols=["AAPL"], limit=10)
    assert result["sources"] == {"finnhub": True, "alphavantage": False}
    assert len(result["items"]) == 2
    assert all(item["sentiment_source"] == "keyword" for item in result["items"])


def test_fetch_news_alphavantage_only(monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "key")

    def fake_get(url, timeout=10.0):
        return {"feed": [{"title": "AAPL rallies", "summary": "", "source": "Bloomberg",
                          "url": "http://z", "time_published": "20260810T143000",
                          "overall_sentiment_score": 0.42,
                          "ticker_sentiment": [{"ticker": "AAPL"}]}]}

    monkeypatch.setattr(news_feed, "_http_get_json", fake_get)
    result = news_feed.fetch_news(symbols=["AAPL"], limit=10)
    assert result["sources"] == {"finnhub": False, "alphavantage": True}
    assert result["items"][0]["sentiment_score"] == 0.42
    assert result["items"][0]["sentiment_source"] == "model"
    assert result["items"][0]["symbols"] == ["AAPL"]


def test_fetch_news_tolerates_source_failure(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "key")

    def boom(url, timeout=10.0):
        raise ValueError("network down")

    monkeypatch.setattr(news_feed, "_http_get_json", boom)
    result = news_feed.fetch_news(symbols=["AAPL"])
    assert result["items"] == []
    assert result["sources"]["finnhub"] is False  # attempted, but failed - not silently claimed


def test_fetch_news_caches_within_ttl(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "key")
    calls = {"n": 0}

    def fake_get(url, timeout=10.0):
        calls["n"] += 1
        return []

    monkeypatch.setattr(news_feed, "_http_get_json", fake_get)
    news_feed.fetch_news(symbols=None, limit=5)
    news_feed.fetch_news(symbols=None, limit=5)
    assert calls["n"] == 1  # second call hit the cache


# ---- sentiment_for_symbol: recency-decayed average -------------------------

def test_sentiment_for_symbol_no_articles_returns_neutral_zero():
    result = news_feed.sentiment_for_symbol("ZZZZ", news_items=[])
    assert result == {"symbol": "ZZZZ", "score": 0.0, "n": 0}


def test_sentiment_for_symbol_averages_relevant_articles_only():
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    items = [
        {"symbols": ["AAPL"], "sentiment_score": 0.8, "published_at": now_iso},
        {"symbols": ["MSFT"], "sentiment_score": -0.9, "published_at": now_iso},  # irrelevant, excluded
    ]
    result = news_feed.sentiment_for_symbol("AAPL", news_items=items)
    assert result["n"] == 1
    assert result["score"] == pytest.approx(0.8, abs=0.01)


def test_sentiment_for_symbol_older_articles_count_less():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    fresh = {"symbols": ["AAPL"], "sentiment_score": 1.0, "published_at": now.isoformat()}
    stale = {"symbols": ["AAPL"], "sentiment_score": -1.0,
            "published_at": (now - timedelta(hours=200)).isoformat()}
    result = news_feed.sentiment_for_symbol("AAPL", news_items=[fresh, stale])
    # the fresh +1.0 article should dominate a 200h-old -1.0 one under a 24h half-life
    assert result["score"] > 0.5
