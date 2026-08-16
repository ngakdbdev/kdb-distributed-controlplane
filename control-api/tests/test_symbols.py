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


def test_live_symbols_rank_before_seed_for_equally_good_prefix_matches():
    # AMZZ-USD (live, injected) shares the "AM" prefix with the real seed
    # entry AMZN - neither is an exact match, so without the live/seed
    # tiebreak this would fall back to plain alphabetical (AMZN < AMZZ-USD)
    # and put the seed entry first even though it has no real price behind
    # it on this deployment. See symbols.py's search() for why that's backwards
    # for a symbol picker whose whole point is finding something tradeable.
    symref.merge_live_symbols(["AMZZ-USD"])
    hits = symref.search("AM", limit=200)
    amzn_idx = next(i for i, r in enumerate(hits) if r["symbol"] == "AMZN")
    live_idx = next(i for i, r in enumerate(hits) if r["symbol"] == "AMZZ-USD")
    assert live_idx < amzn_idx


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


# ---- live symbol discovery (app/symbol_discovery.py) ----------------------
# Uses symbols namespaced ZZTEST* so these tests can't collide with anything
# a real feed might ever publish, or with the seed - symref._live_symbols is
# process-global (module state persists for the whole pytest run), so tests
# here must never pick a symbol another test file's assertions could see.

def test_merge_live_symbols_infers_crypto_market_from_pair_format():
    added = symref.merge_live_symbols(["ZZTEST-USD"])
    assert added == {"ZZTEST-USD"}
    hit = next(r for r in symref.search("ZZTEST-USD") if r["symbol"] == "ZZTEST-USD")
    assert hit["market"] == "CRYPTO" and hit["currency"] == "USD" and hit["source"] == "live"


def test_merge_live_symbols_concatenated_pair_format():
    # Binance/Bybit's own wire format has no separator at all (BTCUSDT)
    symref.merge_live_symbols(["ZZTESTUSDT"])
    hit = next(r for r in symref.search("ZZTESTUSDT") if r["symbol"] == "ZZTESTUSDT")
    assert hit["market"] == "CRYPTO" and hit["currency"] == "USDT"


def test_merge_live_symbols_slash_pair_format():
    symref.merge_live_symbols(["ZZTEST2/EUR"])
    hit = next(r for r in symref.search("ZZTEST2/EUR") if r["symbol"] == "ZZTEST2/EUR")
    assert hit["market"] == "CRYPTO" and hit["currency"] == "EUR"


def test_merge_live_symbols_is_idempotent_and_never_duplicates():
    symref.merge_live_symbols(["ZZTEST3-USD"])
    added_again = symref.merge_live_symbols(["ZZTEST3-USD"])
    assert added_again == set()
    hits = [r for r in symref.search("ZZTEST3-USD") if r["symbol"] == "ZZTEST3-USD"]
    assert len(hits) == 1


def test_merge_live_symbols_never_shadows_the_seed():
    added = symref.merge_live_symbols(["AAPL"])  # already in the seed
    assert added == set()
    hits = [r for r in symref.search("AAPL") if r["symbol"] == "AAPL"]
    assert len(hits) == 1 and hits[0]["source"] == "seed"


def test_live_symbols_appear_in_markets_listing():
    symref.merge_live_symbols(["ZZTEST4-USD"])
    names = {mk["market"] for mk in symref.markets()}
    assert "CRYPTO" in names


def test_symbol_discovery_run_once_merges_from_universe_scan(monkeypatch):
    from app import symbol_discovery
    monkeypatch.setattr(symbol_discovery.signal_engine, "fetch_universe_symbols",
                        lambda connect=None: ["ZZTEST5-USD"])
    symbol_discovery.run_once()
    assert any(r["symbol"] == "ZZTEST5-USD" for r in symref.search("ZZTEST5-USD"))


def test_symbol_discovery_run_once_survives_scan_failure(monkeypatch):
    from app import symbol_discovery
    monkeypatch.setattr(symbol_discovery.signal_engine, "fetch_universe_symbols",
                        lambda connect=None: (_ for _ in ()).throw(ConnectionRefusedError("down")))
    symbol_discovery.run_once()  # must not raise
