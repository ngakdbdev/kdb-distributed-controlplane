"""Tests for the symbol reference search + endpoint."""
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import symbols as symref


def test_search_matches_symbol_and_name():
    assert any(r["symbol"] == "AAPL" for r in symref.search("AAPL"))
    assert any(r["symbol"] == "AAPL" for r in symref.search("apple"))


def test_search_exact_symbol_ranks_first():
    hits = symref.search("BP")
    assert hits[0]["symbol"] == "BP"


def test_search_filters_by_market():
    hits = symref.search("", market="NSE", limit=100)
    assert hits and all(r["market"] == "NSE" for r in hits)


def test_markets_lists_known_exchanges():
    names = {mk["market"] for mk in symref.markets()}
    assert {"NASDAQ", "LSE", "NSE", "XETRA"} <= names


@pytest.fixture()
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture()
def tadmin(client):
    r = client.post("/auth/login", json={"email": "admin@demo-bank.local", "password": "changeme"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_symbol_search_endpoint(client, tadmin):
    r = client.get("/symbols/search?q=RELI", headers=tadmin)
    assert r.status_code == 200, r.text
    assert any(s["symbol"] == "RELIANCE" for s in r.json()["symbols"])


def test_symbols_requires_auth(client):
    assert client.get("/symbols/search").status_code in (401, 403)
