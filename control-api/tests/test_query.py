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


def test_run_query_blocks_write_then_runs_readonly():
    calls = []
    def fake_conn(q):
        calls.append(q)
        return {"sym": ["AAPL"], "price": [1.0]}
    with pytest.raises(ValueError):
        qs.run_query('system "ls"', fake_conn)
    out = qs.run_query("select from trade", fake_conn)
    assert out["kind"] == "table" and calls == ["select from trade"]


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
