"""Tests for signal_forecast.build_time_forecast - the Python port of
web-ui/src/lib/timeForecast.js's buildTimeForecast(). Pure and tested."""
from datetime import datetime, timedelta, timezone

from app.signal_forecast import build_time_forecast

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bars(prices, start=BASE, step_min=1):
    """One trade print per minute bucket, `prices` in order."""
    return [{"time": (start + timedelta(minutes=i * step_min)).isoformat(), "price": p}
            for i, p in enumerate(prices)]


def test_insufficient_raw_rows():
    f = build_time_forecast(_bars([100, 101]))
    assert f["method"] == "insufficient-data"
    assert f["trend"] == "flat"
    assert f["last"] == 101
    assert f["sufficient_data"] is False


def test_no_rows_at_all():
    f = build_time_forecast([])
    assert f["last"] is None and f["method"] == "insufficient-data"


def test_uptrend_classified_up():
    prices = [100 * (1.01 ** i) for i in range(20)]   # ~1%/min compounding
    f = build_time_forecast(_bars(prices))
    assert f["trend"] == "up"
    assert f["drift_per_min"] > 0
    assert f["last"] == prices[-1]
    assert f["method"] == "drift + volatility over 1-minute bars"


def test_downtrend_classified_down():
    prices = [100 * (0.99 ** i) for i in range(20)]
    f = build_time_forecast(_bars(prices))
    assert f["trend"] == "down"
    assert f["drift_per_min"] < 0


def test_flat_prices_classified_flat():
    prices = [100.0] * 20
    f = build_time_forecast(_bars(prices))
    assert f["trend"] == "flat"
    assert f["drift_per_min"] == 0


def test_forecast_points_widen_with_horizon():
    prices = [100 * (1.01 ** i) for i in range(20)]
    f = build_time_forecast(_bars(prices))
    by_h = {p["horizon_min"]: p for p in f["points"]}
    assert set(by_h) == {10, 15, 30, 60}
    # confidence band strictly widens with horizon (spread ~ sqrt(h))
    spread_10 = by_h[10]["upper"] - by_h[10]["lower"]
    spread_60 = by_h[60]["upper"] - by_h[60]["lower"]
    assert spread_60 > spread_10
    assert by_h[10]["expected"] < by_h[60]["expected"]  # positive drift compounds further out


def test_multiple_prints_in_same_minute_bucket_use_last_as_close():
    # three prints inside the same 60s bucket - the bar's close should be the
    # LAST one, matching buildTimeForecast's candlestick-style bucketing
    rows = [
        {"time": BASE.isoformat(), "price": 100},
        {"time": (BASE + timedelta(seconds=20)).isoformat(), "price": 105},
        {"time": (BASE + timedelta(seconds=40)).isoformat(), "price": 102},
    ] + _bars([103, 104, 105, 106, 107], start=BASE + timedelta(minutes=1))
    f = build_time_forecast(rows)
    assert f["sampled_minutes"] == 6  # 1 bucket for the three same-minute prints + 5 more


def test_rows_missing_time_or_price_are_dropped():
    rows = [{"time": None, "price": 100}, {"time": BASE.isoformat(), "price": None}] + _bars([100, 101, 102])
    f = build_time_forecast(rows)
    assert f["last"] == 102


def test_out_of_order_rows_are_sorted_by_time():
    rows = _bars([100, 101, 102])
    shuffled = [rows[2], rows[0], rows[1]]
    f = build_time_forecast(shuffled)
    assert f["last"] == 102  # still the latest by time, not by list order
