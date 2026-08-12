"""Tests for the market-data provider catalog endpoint."""
import pytest
from fastapi.testclient import TestClient

import app.main as m


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login",
                    json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_provider_catalog_lists_live_and_licensed(client, tadmin):
    r = client.get("/connectors/providers", headers=tadmin)
    assert r.status_code == 200, r.text
    cat = {p["name"]: p for p in r.json()}
    assert set(cat) == {"finnhub", "twelvedata", "polygon", "coinbase", "kraken",
                        "binance", "binance-depth", "bybit", "okx",
                        "yahoo", "alphavantage", "nyse", "lseg", "nse", "bse"}
    assert cat["finnhub"]["tier"] == "live"
    assert cat["yahoo"]["tier"] == "live"
    assert cat["nyse"]["tier"] == "licensed"
    assert all(p["requires"] and p["coverage"] for p in cat.values())


def test_provider_catalog_requires_auth(client):
    assert client.get("/connectors/providers").status_code in (401, 403)
