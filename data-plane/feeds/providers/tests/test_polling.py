"""
Tests for the polling providers (Yahoo, Alpha Vantage). An injected `fetch`
returns canned vendor JSON, so a poll cycle is driven with no network, and we
assert it lands as a correctly-shaped, correctly-sharded trade row.
"""
import pytest

from providers import get_provider
from providers.base import ProviderError
from providers.tests.test_registry import FakePublisher


def test_yahoo_poll_publishes_sharded_rows():
    canned = {"quoteResponse": {"result": [
        {"symbol": "AAPL", "regularMarketPrice": 178.1, "regularMarketVolume": 1000,
         "fullExchangeName": "NasdaqGS", "regularMarketTime": 1701234567},
        {"symbol": "TSLA", "regularMarketPrice": 240.0, "regularMarketVolume": 500,
         "fullExchangeName": "NasdaqGS", "regularMarketTime": 1701234567},
    ]}}
    pub = FakePublisher()
    prov = get_provider("yahoo")(["AAPL", "TSLA"], pub, shard_count=2,
                                 fetch=lambda url: canned)
    n = prov.run_once()

    assert n == 2
    rows = {r[1]: r for r in pub.rows}
    assert rows["AAPL"][2] == 178.1 and rows["AAPL"][3] == 1000
    assert rows["AAPL"][5] == "NasdaqGS"
    assert rows["AAPL"][6] == "s0"          # AAPL -> s0 at N=2
    assert len(rows["AAPL"]) == 7


def test_yahoo_url_contains_all_symbols():
    prov = get_provider("yahoo")(["AAPL", "MSFT"], FakePublisher(), shard_count=2,
                                 fetch=lambda url: {})
    url = prov._url()
    assert "AAPL" in url and "MSFT" in url


def test_alphavantage_round_robins_symbols_one_per_poll():
    seen = []

    def fetch(url):
        # capture which symbol was requested, return a matching quote
        sym = url.split("symbol=")[1].split("&")[0]
        seen.append(sym)
        return {"Global Quote": {"01. symbol": sym, "05. price": "10.5", "06. volume": "42"}}

    pub = FakePublisher()
    prov = get_provider("alphavantage")(["AAPL", "MSFT"], pub, shard_count=2,
                                        api_key="demo", fetch=fetch)
    prov.run_once()
    prov.run_once()
    assert seen == ["AAPL", "MSFT"]         # one symbol per poll, round-robin
    assert {r[1] for r in pub.rows} == {"AAPL", "MSFT"}
    assert pub.rows[0][2] == 10.5


def test_alphavantage_needs_key():
    prov = get_provider("alphavantage")(["AAPL"], FakePublisher(), shard_count=2, api_key=None)
    # run_once swallows the error and publishes nothing rather than crashing the loop
    assert prov.run_once() == 0


def test_yahoo_stale_crumb_cleared_on_401_for_next_poll():
    """A 401/403 mid-run means the crumb/cookie went stale - it must be
    dropped so the NEXT poll re-runs the handshake instead of repeating the
    same failure forever."""
    import urllib.error

    prov = get_provider("yahoo")(["AAPL"], FakePublisher(), shard_count=2,
                                 fetch=lambda url: (_ for _ in ()).throw(
                                     urllib.error.HTTPError(url, 401, "unauthorized", {}, None)))
    prov._crumb = "stale-crumb"
    assert prov.run_once() == 0             # logged, not raised (run_once swallows it)
    assert prov._crumb is None              # cleared for the next poll's handshake


def test_polling_run_once_tolerates_fetch_failure():
    def boom(url):
        raise RuntimeError("network down")
    prov = get_provider("yahoo")(["AAPL"], FakePublisher(), shard_count=2, fetch=boom)
    assert prov.run_once() == 0             # logged, not raised


# ---- IBKR (conid resolution + snapshot polling) ----------------------------

def _ibkr_fetch(conid_by_symbol, snapshot_by_conid):
    """Fake CPAPI: routes /iserver/secdef/search (conid lookup) and
    /iserver/marketdata/snapshot (quote poll) to canned responses, the same
    two real endpoints ibkr.py actually calls."""
    def fetch(url):
        if "/iserver/secdef/search" in url:
            sym = url.split("symbol=")[1].split("&")[0]
            conid = conid_by_symbol.get(sym)
            return [{"conid": conid}] if conid else []
        if "/iserver/marketdata/snapshot" in url:
            conids = [int(c) for c in url.split("conids=")[1].split("&")[0].split(",")]
            return [snapshot_by_conid[c] for c in conids if c in snapshot_by_conid]
        raise AssertionError(f"unexpected URL: {url}")
    return fetch


def test_ibkr_resolves_conid_then_polls_snapshot():
    fetch = _ibkr_fetch(
        conid_by_symbol={"AAPL": 265598},
        snapshot_by_conid={265598: {"conid": 265598, "31": "180.23", "87": "1000"}},
    )
    pub = FakePublisher()
    prov = get_provider("ibkr")(["AAPL"], pub, shard_count=2, fetch=fetch)
    n = prov.run_once()
    assert n == 1
    assert pub.rows[0][1] == "AAPL" and pub.rows[0][2] == 180.23 and pub.rows[0][3] == 1000
    assert pub.rows[0][5] == "ibkr"


def test_ibkr_caches_conid_across_polls_only_one_lookup():
    lookups = []

    def fetch(url):
        if "/iserver/secdef/search" in url:
            lookups.append(url)
            return [{"conid": 265598}]
        return [{"conid": 265598, "31": "180.0", "87": "1"}]

    prov = get_provider("ibkr")(["AAPL"], FakePublisher(), shard_count=2, fetch=fetch)
    prov.run_once()
    prov.run_once()
    assert len(lookups) == 1  # resolved once, cached for the second poll


def test_ibkr_symbol_that_fails_to_resolve_is_skipped_not_fatal():
    fetch = _ibkr_fetch(conid_by_symbol={}, snapshot_by_conid={})
    prov = get_provider("ibkr")(["NOPE"], FakePublisher(), shard_count=2, fetch=fetch)
    assert prov.run_once() == 0


def test_ibkr_defaults_gateway_url_when_unset():
    prov = get_provider("ibkr")(["AAPL"], FakePublisher(), shard_count=2)
    assert prov.gateway_base_url == "https://localhost:5000/v1/api"


def test_ibkr_uses_configured_gateway_url():
    prov = get_provider("ibkr")(["AAPL"], FakePublisher(), shard_count=2,
                                gateway_base_url="https://ibeam:5000/v1/api")
    assert prov.gateway_base_url == "https://ibeam:5000/v1/api"
