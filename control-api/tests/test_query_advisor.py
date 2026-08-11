"""Tests for the deterministic (no-LLM) halves of query_advisor.analyze():
shard-routing tips and the scan-risk tip. Regression coverage for a real
incident - a non-aggregated `by` with no symbol filter scanning a table that
had grown to tens of millions of rows hung a request past every timeout;
these are the always-on (no LLM needed) warnings meant to catch that before
the query ever runs."""
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import query_advisor as qa


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_scan_risk_flags_unfiltered_where():
    tip = qa._scan_risk_tip("select from trade where price>100")
    assert tip and "scans the entire table" in tip


def test_scan_risk_flags_non_aggregated_by():
    tip = qa._scan_risk_tip("select price,size by sym from trade")
    assert tip and "no aggregate function" in tip


def test_scan_risk_silent_for_aggregated_by():
    assert qa._scan_risk_tip("select count i, avg price by sym from trade") is None
    assert qa._scan_risk_tip("select vwap:size wavg price by sym from trade") is None


def test_scan_risk_silent_when_narrowed_by_symbol():
    assert qa._scan_risk_tip("select from trade where sym=`AAPL") is None
    assert qa._scan_risk_tip("select price,size by sym from trade where sym in `AAPL`MSFT") is None


def test_scan_risk_silent_for_plain_select_no_where_no_by():
    # select from t (no where/by) is its own, separate concern - already
    # flagged by nl2q's/query_advisor's LLM-side prompt as an info note, and
    # bounded client-side now by query_service._cap_result_rows regardless
    assert qa._scan_risk_tip("select from trade") is None


def test_scan_risk_silent_for_non_select():
    assert qa._scan_risk_tip("tables[]") is None
    assert qa._scan_risk_tip(".gw.health[]") is None


def test_analyze_endpoint_includes_scan_risk_tip(client, tadmin):
    r = client.post("/query/analyze", json={"q": "select price by sym from trade", "target": "gateway"},
                     headers=tadmin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert any("no aggregate function" in i for i in body["issues"])
