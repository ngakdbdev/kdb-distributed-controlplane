"""Tests for the query workspace: read-only guard, result shaping, endpoint."""
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import query_service as qs


# ---- read-only guard ------------------------------------------------------

@pytest.mark.parametrize("q", [
    "select from trade",
    "select from trade where sym=`AAPL",
    ".gw.health[]",
    "select avg price by sym from trade",
    "tables[]",
])
def test_readonly_allows_selects(q):
    ok, _ = qs.check_readonly(q)
    assert ok, q


@pytest.mark.parametrize("q", [
    'system "ls"',
    "hopen `:/etc/passwd",
    "`:/tmp/x set trade",
    "read0 `:/etc/passwd",
    "\\l something.q",
    "value \"exit 0\"",
    "hdel `:data",
    ".z.pw:{[u;p] 1b}",
    "`t upsert (1;2)",
])
def test_readonly_blocks_dangerous(q):
    ok, reason = qs.check_readonly(q)
    assert not ok and reason, q


def test_empty_query_blocked():
    ok, _ = qs.check_readonly("   ")
    assert not ok


# ---- limit clamp ----------------------------------------------------------

def test_clamp_limit():
    assert qs.clamp_limit(50) == 50
    assert qs.clamp_limit(0) == 1
    assert qs.clamp_limit(10 ** 9) == qs.MAX_ROW_LIMIT
    assert qs.clamp_limit("nonsense") == qs.DEFAULT_ROW_LIMIT


# ---- result shaping -------------------------------------------------------

def test_shape_table_dict():
    res = qs.shape_result({"sym": ["AAPL", "MSFT"], "price": [1.0, 2.0]})
    assert res["kind"] == "table"
    assert res["columns"] == ["sym", "price"]
    assert res["rows"] == [["AAPL", 1.0], ["MSFT", 2.0]]
    assert res["row_count"] == 2 and res["truncated"] is False


def test_shape_table_truncates_to_limit():
    res = qs.shape_result({"x": list(range(100))}, limit=10)
    assert len(res["rows"]) == 10 and res["row_count"] == 100 and res["truncated"] is True


def test_shape_plain_dict_becomes_key_value():
    res = qs.shape_result({"a": 1, "b": 2})
    assert res["kind"] == "dict" and res["columns"] == ["key", "value"]


def test_shape_vector_and_scalar():
    v = qs.shape_result([1, 2, 3])
    assert v["kind"] == "vector" and v["columns"] == ["value"] and len(v["rows"]) == 3
    s = qs.shape_result(42)
    assert s["kind"] == "scalar" and s["rows"] == [[42]]


def test_shape_bare_numpy_array_is_a_vector_not_a_stringified_blob():
    # Regression: `cols\`trade` comes back from qpython as a plain (non-
    # structured) numpy array of bytes, e.g. array([b'time', b'sym', ...]) -
    # confirmed against a live cluster this used to fall through every
    # shape_result branch to the scalar fallback and render as the literal
    # text "[b'time' b'sym' ...]" instead of a proper one-row-per-name grid,
    # which is exactly what broke the query workspace's schema-aware
    # autocomplete (it reads this via a `cols` query).
    import numpy as np
    arr = np.array([b"time", b"sym", b"price"])
    out = qs.shape_result(arr)
    assert out["kind"] == "vector"
    assert out["columns"] == ["value"]
    assert out["rows"] == [["time"], ["sym"], ["price"]]


def test_shape_zero_dim_numpy_array_still_a_scalar():
    import numpy as np
    out = qs.shape_result(np.array(42))
    assert out["kind"] == "scalar"
    assert out["rows"] == [[42]]


# ---- kdb+ timestamp/date conversion ---------------------------------------
# Regression coverage for a real bug: qpython (pandas=False) hands timestamp
# columns back as plain int64 nanoseconds-since-2000.01.01, not numpy
# datetime64 - with nothing converting that, the raw integer reached the UI
# and `new Date(hugeInt)` rendered as "Invalid Date" (confirmed live against
# an RDB: meta.time == -12, the q type code for timestamp).

class _FakeDtype:
    def __init__(self, names):
        self.names = names


class _FakeMeta:
    def __init__(self, **kw):
        self._d = kw

    def as_dict(self):
        return self._d


class _FakeStructuredArray:
    """Minimal stand-in for qpython's QTable: dtype.names + per-column
    access + a .meta exposing each column's q type code."""
    def __init__(self, columns: dict, qtypes: dict):
        self._columns = columns
        self.dtype = _FakeDtype(tuple(columns.keys()))
        self.meta = _FakeMeta(**qtypes)

    def __getitem__(self, name):
        return self._columns[name]


def test_kdb_temporal_to_iso_timestamp():
    # 2026-08-08T10:26:55.874225 UTC, as raw ns-since-2000.01.01 (qtype -12)
    ns_since_2000 = int((__import__("datetime").datetime(2026, 8, 8, 10, 26, 55, 874225)
                        - __import__("datetime").datetime(2000, 1, 1)).total_seconds() * 1_000_000_000)
    iso = qs._kdb_temporal_to_iso(ns_since_2000, -12)
    assert iso == "2026-08-08T10:26:55.874225"

def test_kdb_temporal_to_iso_date():
    days_since_2000 = (__import__("datetime").date(2026, 8, 8) - __import__("datetime").date(2000, 1, 1)).days
    assert qs._kdb_temporal_to_iso(days_since_2000, -14) == "2026-08-08"

def test_kdb_temporal_to_iso_passes_through_non_temporal_types():
    assert qs._kdb_temporal_to_iso(12345, -7) == 12345   # a `long` column - not a date/timestamp

def test_kdb_temporal_to_iso_none_passes_through():
    assert qs._kdb_temporal_to_iso(None, -12) is None

def test_shape_result_converts_timestamp_column_from_structured_array():
    # exactly the shape that broke: select time, price, size from trade
    ns = int((__import__("datetime").datetime(2026, 8, 8, 10, 0, 0)
              - __import__("datetime").datetime(2000, 1, 1)).total_seconds() * 1_000_000_000)
    arr = _FakeStructuredArray(
        {"time": [ns], "price": [114.98], "size": [300]},
        {"qtype": 98, "time": -12, "price": -9, "size": -7},
    )
    res = qs.shape_result(arr)
    assert res["columns"] == ["time", "price", "size"]
    time_val = res["rows"][0][res["columns"].index("time")]
    assert time_val == "2026-08-08T10:00:00"
    # a JS `new Date(...)` must be able to parse what we send - guard against
    # ever regressing back to a bare epoch integer
    assert isinstance(time_val, str) and "T" in time_val

def test_shape_result_leaves_non_temporal_int_columns_alone():
    arr = _FakeStructuredArray({"size": [100, 200]}, {"qtype": 98, "size": -7})
    res = qs.shape_result(arr)
    assert res["rows"] == [[100], [200]]

def test_shape_result_structured_array_without_meta_falls_back_safely():
    """Some structured results may have no .meta at all - must not crash,
    just skip the conversion (better a raw number than a 500)."""
    arr = _FakeStructuredArray({"x": [1, 2]}, {})
    arr.meta = None
    res = qs.shape_result(arr)
    assert res["rows"] == [[1], [2]]


def test_shape_result_qkeyedtable_from_grouped_query():
    """Regression: `select ... by ... from t` (any grouped select) comes
    back from qpython as a QKeyedTable - a SEPARATE class from QDictionary,
    not a subclass of it (confirmed against qpython's source), despite
    being structurally identical (.keys/.values, each a QTable). Without
    this, `select count i by sym from trade` fell through every branch in
    shape_result to the scalar fallback and rendered as one row of raw
    repr text ("[(b'AAPL',) ...]![(3810,) ...]") instead of a sym|cnt grid
    - confirmed live against a real cluster."""
    import numpy as np
    from qpython.qcollection import QKeyedTable, qlist, qtable
    from qpython.qtype import QLONG_LIST, QSYMBOL_LIST

    keys = qtable(["sym"], [qlist(np.array(["AAPL", "MSFT"]), qtype=QSYMBOL_LIST)])
    values = qtable(["cnt"], [qlist(np.array([3810, 3887]), qtype=QLONG_LIST)])
    res = qs.shape_result(QKeyedTable(keys, values))
    assert res["kind"] == "table"
    assert res["columns"] == ["k_sym", "cnt"]
    assert res["rows"] == [["AAPL", 3810], ["MSFT", 3887]]


def test_run_query_blocks_write_then_runs_readonly():
    calls = []
    def fake_conn(q):
        calls.append(q)
        return {"sym": ["AAPL"], "price": [1.0]}
    with pytest.raises(ValueError):
        qs.run_query('system "ls"', fake_conn)
    out = qs.run_query("select from trade", fake_conn)
    # the query actually sent to q is capped (see test_cap_result_rows_* below) -
    # `run_query`'s payload["query"] still reports the user's original text
    assert out["kind"] == "table" and out["query"] == "select from trade"
    assert calls == [f"{qs.DEFAULT_ROW_LIMIT + 1} sublist (select from trade)"]


# ---- row-cap pushdown (query_service._cap_result_rows) --------------------
# Regression coverage for two real incidents on the same mechanism:
# 1) `select from trade` against a live RDB that had grown to 16M+ rows hung
#    the whole HTTP request for 30s+ (past every client/server timeout)
#    because the row limit was only ever applied client-side, AFTER pulling
#    the entire result over IPC. Pushing a take into the query itself bounds
#    what kdb+ computes/returns in the first place.
# 2) That take was originally `#`, not `sublist` - `#` on a table CYCLES/
#    pads when the real result has fewer rows than the cap (plain q list
#    semantics), so `select count i from trade` (one real row) came back
#    with that one row repeated out to `limit` rows. See test_query_service
#    for the dedicated regression test of that bug.

def test_cap_result_rows_wraps_a_plain_select():
    assert qs._cap_result_rows("select from trade", 50) == "50 sublist (select from trade)"
    assert qs._cap_result_rows("  select sym from trade  ", 10) == "10 sublist (select sym from trade)"


def test_cap_result_rows_case_insensitive():
    assert qs._cap_result_rows("SELECT from trade", 5) == "5 sublist (SELECT from trade)"


def test_cap_result_rows_leaves_an_explicit_take_alone():
    # the caller already chose their own take - respect it, don't double-wrap
    assert qs._cap_result_rows("100#select from trade by sym", 50) == "100#select from trade by sym"
    assert qs._cap_result_rows("-5#trade", 50) == "-5#trade"


def test_cap_result_rows_leaves_non_select_queries_alone():
    # exec/functional-form/system calls have their own semantics - wrapping
    # them isn't safe, and non-select expressions (tables[], .gw.health[])
    # were never the source of the runaway-fetch problem this guards against
    for q in ["tables[]", ".gw.health[]", "exec sym from trade", "update x:1 from trade"]:
        assert qs._cap_result_rows(q, 50) == q


def test_run_query_caps_at_limit_plus_one_so_truncation_is_still_detectable():
    calls = []
    def fake_conn(q):
        calls.append(q)
        return {"x": list(range(9999))}  # fake conn ignores the cap - just proving what was asked for
    qs.run_query("select from trade", fake_conn, limit=20)
    assert calls == ["21 sublist (select from trade)"]


def test_cap_result_rows_does_not_pad_a_short_aggregate_result():
    # the actual bug reported live: `select count i from trade` naturally
    # returns exactly one row - `#` would have taken that one row and
    # cycled/padded it out to `limit` rows, all showing the same count.
    # sublist must never do that: fewer real rows than the cap should stay
    # fewer rows, not get padded up to it.
    assert qs._cap_result_rows("select count i from trade", 2000) == \
        "2000 sublist (select count i from trade)"


# ---- federation / combine_results ----------------------------------------

def test_combine_unions_rows_with_provenance():
    g1 = qs.shape_result({"sym": ["AAPL"], "price": [1.0]})
    g2 = qs.shape_result({"sym": ["MSFT"], "price": [2.0]})
    out = qs.combine_results([("gw-emea", g1), ("gw-apac", g2)])
    assert out["kind"] == "federated" and out["target_count"] == 2
    assert out["columns"] == ["_target", "sym", "price"]
    assert out["rows"] == [["gw-emea", "AAPL", 1.0], ["gw-apac", "MSFT", 2.0]]
    assert out["row_count"] == 2


def test_combine_outer_joins_mismatched_columns():
    g1 = qs.shape_result({"sym": ["AAPL"], "price": [1.0]})
    g2 = qs.shape_result({"sym": ["MSFT"], "venue": ["X"]})
    out = qs.combine_results([("a", g1), ("b", g2)], add_provenance=False)
    assert out["columns"] == ["sym", "price", "venue"]
    # missing cells filled with None
    assert out["rows"][0] == ["AAPL", 1.0, None]
    assert out["rows"][1] == ["MSFT", None, "X"]


def test_combine_respects_limit():
    g = qs.shape_result({"x": list(range(10))})
    out = qs.combine_results([("a", g), ("b", g)], limit=5)
    assert len(out["rows"]) == 5 and out["truncated"] is True


# ---- endpoint (guard path; no live q needed) -----------------------------

@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_targets_lists_gateway_and_shards(client, tadmin):
    r = client.get("/query/targets", headers=tadmin)
    assert r.status_code == 200, r.text
    ids = [t["id"] for t in r.json()["targets"]]
    assert "gateway" in ids and any(i.startswith("rdb-") for i in ids)


def test_targets_includes_idb_and_hdb_shards(client, tadmin):
    # historical tiers must be reachable as explicit targets, not just rdb -
    # see query_router.route_tiers for why the gateway path also needs this
    r = client.get("/query/targets", headers=tadmin)
    ids = [t["id"] for t in r.json()["targets"]]
    assert any(i.startswith("idb-") for i in ids)
    assert any(i.startswith("hdb-") for i in ids)


def test_gateway_path_switches_to_hdb_for_a_date_range_query(monkeypatch):
    """A market-table query whose `date` clause needs history (not provably
    today-only) must fan out to hdb targets - this is the fix for a query
    like a multi-month VWAP silently only ever touching today's rdb buffer
    (and returning nothing for the historical portion) because hdb was
    unreachable. NOT rdb, NOT idb - confirmed live against this schema that
    neither has a `date` column at all, so sending a date-filtered query to
    either fails outright rather than just wasting a round trip - see
    query_router.route_tiers' docstring."""
    from app.routers import query as query_module

    captured = {}

    def fake_run_many(target_ids, query, limit, allow_write, max_allowed=None):
        captured["target_ids"] = target_ids
        return [{"target": t, "ok": False, "error": "no live q in this test",
                 "elapsed_ms": 0.0} for t in target_ids]

    monkeypatch.setattr(query_module, "_run_many", fake_run_many)
    query_module._run_one("gateway", "select vwap:size wavg price by sym from trade "
                                     "where date within (2024.01.01;2024.03.31)", 100, False)
    assert captured["target_ids"], "expected a fan-out to be attempted"
    prefixes = {t.split("-")[0] for t in captured["target_ids"]}
    assert prefixes == {"hdb"}


def test_gateway_path_stays_rdb_only_without_a_historical_date_clause(monkeypatch):
    """Unchanged behavior for the common case: no `date` clause at all keeps
    the exact same rdb-only cost profile it had before idb/hdb were
    reachable - no surprise perf regression for existing queries."""
    from app.routers import query as query_module

    captured = {}

    def fake_run_many(target_ids, query, limit, allow_write, max_allowed=None):
        captured["target_ids"] = target_ids
        return [{"target": t, "ok": False, "error": "no live q in this test",
                 "elapsed_ms": 0.0} for t in target_ids]

    monkeypatch.setattr(query_module, "_run_many", fake_run_many)
    query_module._run_one("gateway", "select from trade where sym=`AAPL", 100, False)
    prefixes = {t.split("-")[0] for t in captured["target_ids"]}
    assert prefixes == {"rdb"}


def test_run_many_dispatches_targets_concurrently(monkeypatch):
    """_run_many must actually overlap the work, not just serially wrap
    _run_one - the whole point of parallelizing shard/tier fan-out for a
    wide historical scan is that N targets take roughly as long as the
    slowest one, not the sum of all of them."""
    import time as time_mod
    from app.routers import query as query_module

    def slow_run_one(target_id, query, limit, allow_write, max_allowed=None):
        time_mod.sleep(0.2)
        return {"target": target_id, "ok": True,
                "grid": {"columns": [], "rows": [], "row_count": 0},
                "elapsed_ms": 200.0}

    monkeypatch.setattr(query_module, "_run_one", slow_run_one)
    targets = [f"hdb-s{i}" for i in range(6)]
    t0 = time_mod.perf_counter()
    results = query_module._run_many(targets, "select from trade", 100, False)
    elapsed = time_mod.perf_counter() - t0
    assert len(results) == len(targets)
    assert all(r["ok"] for r in results)
    # 6 targets x 0.2s each: serial would be >=1.2s; concurrent should be
    # well under that even accounting for thread-pool/CI scheduling slack
    assert elapsed < 0.8, f"expected concurrent dispatch, took {elapsed:.2f}s"


def test_run_rejects_dangerous_query_before_connecting(client, tadmin):
    r = client.post("/query/run", json={"target": "gateway", "query": 'system "ls"'}, headers=tadmin)
    assert r.status_code == 400
    assert "read-only" in r.json()["detail"]


def test_run_rejects_write_flag_when_disabled(client, tadmin):
    r = client.post("/query/run",
                    json={"target": "gateway", "query": "`t set 1", "allow_write": True},
                    headers=tadmin)
    assert r.status_code in (400, 403)


def test_query_requires_auth(client):
    assert client.get("/query/targets").status_code in (401, 403)


# ---- parquet export (pure - no live q needed, operates on a grid already in hand) --

def test_export_parquet_roundtrips_real_file(client, tadmin):
    import io
    import pyarrow.parquet as pq

    body = {
        "columns": ["sym", "price", "size"],
        "rows": [["AAPL", 189.5, 100], ["MSFT", 412.1, 50], ["AAPL", None, 25]],
    }
    r = client.post("/query/export/parquet", json=body, headers=tadmin)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/octet-stream"
    assert ".parquet" in r.headers["content-disposition"]

    table = pq.read_table(io.BytesIO(r.content))
    assert table.column_names == ["sym", "price", "size"]
    assert table.num_rows == 3
    assert table.column("sym").to_pylist() == ["AAPL", "MSFT", "AAPL"]
    assert table.column("price").to_pylist() == [189.5, 412.1, None]


def test_export_parquet_mixed_type_column_falls_back_to_string(client, tadmin):
    import io
    import pyarrow.parquet as pq

    # a column pyarrow can't infer one type for (str + int in the same column)
    # must degrade to a text column, not fail the whole export.
    body = {"columns": ["weird"], "rows": [["a"], [1], [None]]}
    r = client.post("/query/export/parquet", json=body, headers=tadmin)
    assert r.status_code == 200, r.text
    table = pq.read_table(io.BytesIO(r.content))
    assert table.column("weird").to_pylist() == ["a", "1", None]


def test_export_parquet_rejects_empty_grid(client, tadmin):
    r = client.post("/query/export/parquet", json={"columns": ["x"], "rows": []}, headers=tadmin)
    assert r.status_code == 400

    r = client.post("/query/export/parquet", json={"columns": [], "rows": [[1]]}, headers=tadmin)
    assert r.status_code == 400


def test_export_parquet_requires_auth(client):
    r = client.post("/query/export/parquet", json={"columns": ["x"], "rows": [[1]]})
    assert r.status_code in (401, 403)


def test_export_parquet_rejects_over_10gb(client, tadmin, monkeypatch):
    from app.routers import query as query_module

    def _too_big(columns, rows):
        raise query_module.parquet_export.ExportTooLarge(11 * 1024**3)

    monkeypatch.setattr(query_module.parquet_export, "write_parquet_bytes", _too_big)
    r = client.post("/query/export/parquet", json={"columns": ["x"], "rows": [[1]]}, headers=tadmin)
    assert r.status_code == 413
    assert "S3/ADLS" in r.json()["detail"]


# ---- background export (S3/ADLS) - validation only; the actual job run is --
# ---- covered by test_export_jobs.py with fakes, no live q/cloud needed -----

def test_background_export_rejects_bad_provider(client, tadmin):
    r = client.post("/query/export/background", json={
        "target": "gateway", "query": "select from trade",
        "destination": {"provider": "gcs", "bucket": "x"},
    }, headers=tadmin)
    assert r.status_code == 400
    assert "provider" in r.json()["detail"]


def test_background_export_rejects_s3_without_key(client, tadmin):
    r = client.post("/query/export/background", json={
        "target": "gateway", "query": "select from trade",
        "destination": {"provider": "s3", "bucket": "x"},
    }, headers=tadmin)
    assert r.status_code == 400
    assert "key" in r.json()["detail"]


def test_background_export_rejects_write_query(client, tadmin):
    r = client.post("/query/export/background", json={
        "target": "gateway", "query": "`t set 1",
        "destination": {"provider": "s3", "bucket": "x", "key": "y.parquet"},
    }, headers=tadmin)
    assert r.status_code == 400
    assert "read-only" in r.json()["detail"]


def test_background_export_rejects_unknown_target(client, tadmin):
    r = client.post("/query/export/background", json={
        "target": "not-a-real-target", "query": "select from trade",
        "destination": {"provider": "s3", "bucket": "x", "key": "y.parquet"},
    }, headers=tadmin)
    assert r.status_code == 404


def test_export_jobs_requires_auth(client):
    assert client.get("/query/export/jobs").status_code in (401, 403)
    assert client.get("/query/export/jobs/nonexistent").status_code in (401, 403)


def test_get_export_job_not_found(client, tadmin):
    r = client.get("/query/export/jobs/nonexistent-id", headers=tadmin)
    assert r.status_code == 404
