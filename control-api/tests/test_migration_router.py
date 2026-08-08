"""Tests for the migration-assessment + TCO endpoints."""
import pytest
from fastapi.testclient import TestClient

import app.main as m


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_analyze_requires_auth(client):
    assert client.post("/migration/analyze", json={"files": []}).status_code in (401, 403)

def test_analyze_rejects_empty_file_list(client, tadmin):
    r = client.post("/migration/analyze", headers=tadmin, json={"files": []})
    assert r.status_code == 400

def test_analyze_returns_effort_report(client, tadmin):
    body = {"files": [{"name": "tick.q",
                       "content": "trade:([] time:`timestamp$(); sym:`symbol$())\n.z.pg:{[m] value m}"}]}
    r = client.post("/migration/analyze", headers=tadmin, json=body)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["totals"]["scripts"] == 1
    assert report["effort"]["size"] in ("S", "M", "L", "XL")
    assert any(".z.pg" in f for f in report["effort"]["factors"])


def test_tco_rates_endpoint_lists_defaults(client, tadmin):
    r = client.get("/migration/tco/rates", headers=tadmin)
    assert r.status_code == 200
    assert "m7i.2xlarge" in r.json()["cloud_hourly_usd"]

def test_tco_endpoint_estimates_from_high_level_choices(client, tadmin):
    body = {"location": "aws", "profile": "balanced", "shard_ranges": "a-z"}
    r = client.post("/migration/tco", headers=tadmin, json=body)
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["monthly_infra_usd"] > 0
    assert "estimated_annual_savings_usd" not in result

def test_tco_endpoint_with_current_cost_and_rate_override(client, tadmin):
    body = {"location": "onprem", "profile": "balanced", "shard_ranges": "a-z",
           "current_annual_cost": 200000}
    r = client.post("/migration/tco", headers=tadmin, json=body)
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["current_annual_cost_usd"] == 200000
    assert "estimated_annual_savings_usd" in result

def test_tco_endpoint_rejects_bad_shard_ranges(client, tadmin):
    body = {"location": "aws", "profile": "balanced", "shard_ranges": "z-a"}
    r = client.post("/migration/tco", headers=tadmin, json=body)
    assert r.status_code == 400
