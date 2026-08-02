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
