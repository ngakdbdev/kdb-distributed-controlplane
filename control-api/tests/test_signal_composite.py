"""Tests for signal_composite.py - each of the five factors individually
(pure math, checkable against known inputs), then the weighted blend
including the "missing factor drops out and weights renormalize" behavior."""
import pytest

from app import signal_composite as sc


# ---- _zscore ---------------------------------------------------------------

def test_zscore_of_constant_series_is_all_zero():
    assert sc._zscore([5, 5, 5, 5]) == [0.0, 0.0, 0.0, 0.0]


def test_zscore_centers_and_scales():
    z = sc._zscore([1, 2, 3, 4, 5])
    assert z[2] == pytest.approx(0.0, abs=1e-9)  # the mean maps to 0
    assert z[0] < 0 < z[-1]


def test_zscore_empty_list():
    assert sc._zscore([]) == []


# ---- mean_reversion_zscore --------------------------------------------------

def test_mean_reversion_none_with_insufficient_history():
    assert sc.mean_reversion_zscore([1, 2, 3], lookback=20) is None


def test_mean_reversion_price_stretched_above_mean_is_bearish_negative():
    # window oscillates mildly around 100 (real variance, so the z-score
    # isn't degenerate), last print jumps well above it -> stretched ABOVE
    # mean -> factor should be NEGATIVE (oriented bullish-positive, see
    # docstring)
    window = [100.0, 101.0, 99.0, 100.5, 99.5] * 4
    val = sc.mean_reversion_zscore(window + [110.0], lookback=20)
    assert val is not None and val < 0


def test_mean_reversion_price_stretched_below_mean_is_bullish_positive():
    window = [100.0, 101.0, 99.0, 100.5, 99.5] * 4
    val = sc.mean_reversion_zscore(window + [90.0], lookback=20)
    assert val is not None and val > 0


def test_mean_reversion_zero_stdev_window_returns_zero_not_nan():
    prices = [100.0] * 21  # perfectly flat, including the last print
    assert sc.mean_reversion_zscore(prices, lookback=20) == 0.0


# ---- order_flow_imbalance ---------------------------------------------------

def test_ofi_none_with_fewer_than_two_rows():
    assert sc.order_flow_imbalance([{"price": 1.0, "size": 10}]) is None


def test_ofi_all_upticks_is_fully_positive():
    rows = [{"price": p, "size": 10} for p in [100, 101, 102, 103]]
    assert sc.order_flow_imbalance(rows) == pytest.approx(1.0)


def test_ofi_all_downticks_is_fully_negative():
    rows = [{"price": p, "size": 10} for p in [103, 102, 101, 100]]
    assert sc.order_flow_imbalance(rows) == pytest.approx(-1.0)


def test_ofi_unchanged_price_keeps_previous_direction():
    # up, then flat (should still count as buy-classified per the tick rule), then down
    rows = [{"price": 100, "size": 10}, {"price": 101, "size": 10},
           {"price": 101, "size": 10}, {"price": 100, "size": 10}]
    val = sc.order_flow_imbalance(rows)
    # 2 buy-classified (101, 101) + 1 sell-classified (100) = (20-10)/30
    assert val == pytest.approx((20.0 - 10.0) / 30.0)


def test_ofi_zero_total_volume_returns_zero():
    rows = [{"price": 100, "size": 0}, {"price": 101, "size": 0}]
    assert sc.order_flow_imbalance(rows) == 0.0


# ---- composite_signal: the weighted blend ----------------------------------

def _candidate(symbol, drift=0.0, vol=0.1, prices=None, rows=None, news=None):
    return {
        "symbol": symbol, "drift_per_min": drift, "realized_vol_annualized": vol,
        "prices": prices or [], "trade_rows": rows or [], "news_sentiment": news,
    }


def test_composite_signal_ranks_stronger_momentum_higher():
    candidates = [_candidate("WEAK", drift=0.01), _candidate("STRONG", drift=1.0)]
    scored = sc.composite_signal(candidates)
    assert scored[0]["symbol"] == "STRONG"  # sorted descending by composite_score


def test_composite_signal_missing_factors_drop_out_not_scored_as_bearish():
    # no prices/rows/news at all -> only momentum/vol_scaled_momentum are
    # "available"; mean_reversion/order_flow/news must be marked unavailable,
    # not silently folded in as a 0 (bearish) at full weight
    candidates = [_candidate("A", drift=0.5), _candidate("B", drift=-0.5)]
    scored = sc.composite_signal(candidates)
    for row in scored:
        assert row["factors"]["mean_reversion"]["available"] is False
        assert row["factors"]["order_flow_imbalance"]["available"] is False
        assert row["factors"]["news_sentiment"]["available"] is False
        assert row["factors"]["momentum"]["available"] is True


def test_composite_signal_weights_sum_to_roughly_one():
    scored = sc.composite_signal([_candidate("A", drift=0.1)])
    total = sum(f["weight"] for f in scored[0]["factors"].values())
    assert total == pytest.approx(1.0, abs=0.01)


def test_composite_signal_custom_weights_override_defaults():
    candidates = [_candidate("A", drift=1.0, prices=[100.0] * 21)]
    scored = sc.composite_signal(candidates, weights={"momentum": 10.0})
    # momentum's weight share should now dominate the breakdown
    assert scored[0]["factors"]["momentum"]["weight"] > 0.5


def test_composite_signal_all_factors_present_produces_a_finite_score():
    prices = [100.0 + i * 0.1 for i in range(25)]
    rows = [{"price": p, "size": 10} for p in prices]
    candidates = [_candidate("A", drift=0.3, vol=0.2, prices=prices, rows=rows, news=0.4)]
    scored = sc.composite_signal(candidates)
    assert isinstance(scored[0]["composite_score"], float)
    for factor in scored[0]["factors"].values():
        assert factor["available"] is True
