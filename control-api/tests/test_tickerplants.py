"""Tests for the per-tickerplant monitor's qpython-value normalization.

This module had zero test coverage before this file - and it was silently
broken: `.u.stats[]`'s lastTs comes back from qpython wrapped in QTemporal
(holding a numpy.datetime64), which neither the old _scalar() nor FastAPI's
default JSON encoder can serialize. Confirmed live: hitting /tickerplants
500'd with "'numpy.datetime64' object is not iterable" before the fix below.
"""
import numpy as np
import pytest
from qpython.qtemporal import QTemporal

from app.routers.tickerplants import _norm, _scalar


def _qtemporal(dt64, qtype=-12):
    qt = QTemporal(np.datetime64(dt64))
    qt._meta_init(qtype=qtype)
    return qt


def test_scalar_converts_qtemporal_timestamp_to_string():
    qt = _qtemporal("2026-08-08T14:32:23.729603180")
    out = _scalar(qt)
    assert out == "2026-08-08T14:32:23.729603180"
    assert isinstance(out, str)   # must be JSON-serializable, not the QTemporal object

def test_scalar_converts_bare_numpy_datetime64():
    out = _scalar(np.datetime64("2026-08-08T00:00:00"))
    assert isinstance(out, str) and out.startswith("2026-08-08")

def test_scalar_converts_bare_numpy_timedelta64():
    out = _scalar(np.timedelta64(5, "s"))
    assert isinstance(out, str)

def test_scalar_unwraps_numpy_generic_scalars():
    assert _scalar(np.int64(42)) == 42
    assert isinstance(_scalar(np.int64(42)), int)

def test_scalar_passes_through_plain_values():
    assert _scalar("s0") == "s0"
    assert _scalar(5) == 5
    assert _scalar(True) is True

def test_scalar_decodes_bytes():
    assert _scalar(b"s0") == "s0"

def test_norm_dict_with_qtemporal_value_is_json_serializable():
    """The actual shape .u.stats[] normalizes into: a flat dict with a
    QTemporal in one slot. Every value in the output must survive json.dumps."""
    import json
    stats = {"shard": b"s0", "recv": np.int64(527119), "lastTs": _qtemporal("2026-08-08T14:32:23.729603")}
    result = _norm(stats)
    assert result["lastTs"] == "2026-08-08T14:32:23.729603"
    json.dumps(result)   # must not raise

def test_norm_handles_nested_numpy_array():
    result = _norm(np.array([1, 2, 3]))
    assert result == [1, 2, 3]

@pytest.mark.parametrize("v", [
    np.datetime64("2026-08-08"),
    np.timedelta64(1, "h"),
    np.int64(1), np.float64(1.5),
])
def test_every_numpy_temporal_and_generic_is_json_serializable(v):
    import json
    json.dumps(_scalar(v))   # must not raise for any of these
