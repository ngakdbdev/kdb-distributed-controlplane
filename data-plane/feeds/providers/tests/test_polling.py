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


def test_polling_run_once_tolerates_fetch_failure():
    def boom(url):
        raise RuntimeError("network down")
    prov = get_provider("yahoo")(["AAPL"], FakePublisher(), shard_count=2, fetch=boom)
    assert prov.run_once() == 0             # logged, not raised
