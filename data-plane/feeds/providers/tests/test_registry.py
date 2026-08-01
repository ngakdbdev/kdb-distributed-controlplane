"""
Tests for the provider registry/catalog and the publish plumbing.

The live adapters' _handle_raw is driven with a fake publisher (no websocket),
proving a vendor frame lands as a correctly-shaped, correctly-sharded trade
row. Uses the real topology module for routing (tests run from data-plane/feeds).
"""
import pytest

import providers
from providers import catalog, get_provider
from providers.base import ProviderNotConfigured, ProviderError


class FakePublisher:
    def __init__(self):
        self.rows = []

    def publish_rows(self, table, rows):
        self.table = table
        self.rows.extend(rows)


# ---- registry / catalog --------------------------------------------------

def test_catalog_lists_all_providers_with_correct_tiers():
    cat = {p["name"]: p for p in catalog()}
    assert set(cat) == {"finnhub", "twelvedata", "polygon", "yahoo", "alphavantage",
                        "nyse", "lseg", "nse", "bse"}
    live = {n for n, p in cat.items() if p["live"]}
    assert live == {"finnhub", "twelvedata", "polygon", "yahoo", "alphavantage"}
    # every provider advertises what it needs
    assert all(cat[n]["requires"] for n in cat)


def test_get_provider_unknown_raises():
    with pytest.raises(KeyError):
        get_provider("bloomberg")


# ---- live adapter publish path -------------------------------------------

def test_finnhub_frame_publishes_sharded_trade_row():
    pub = FakePublisher()
    prov = get_provider("finnhub")(["AAPL"], pub, shard_count=2)
    n = prov._handle_raw('{"type":"trade","data":[{"s":"AAPL","p":178.1,"v":100,"t":1701234567000}]}')

    assert n == 1
    assert pub.table == "trade"
    row = pub.rows[0]
    # [ts, sym, price, size, side, venue, shard]
    assert row[1] == "AAPL" and row[2] == 178.1 and row[3] == 100
    assert row[5] == "finnhub"
    assert row[6] == "s0"          # AAPL routes to shard s0 at N=2
    assert len(row) == 7


def test_polygon_batch_routes_each_symbol_to_its_shard():
    import topology
    pub = FakePublisher()
    prov = get_provider("polygon")(["AAPL", "TSLA"], pub, shard_count=2)
    prov._handle_raw('[{"ev":"T","sym":"AAPL","p":1.0,"s":1,"t":1,"x":11},'
                     ' {"ev":"T","sym":"TSLA","p":2.0,"s":2,"t":2,"x":4}]')
    shards = {row[1]: row[6] for row in pub.rows}
    assert shards["AAPL"] == topology.shard_of("AAPL", 2)
    assert shards["TSLA"] == topology.shard_of("TSLA", 2)


def test_bad_json_frame_is_ignored():
    pub = FakePublisher()
    prov = get_provider("twelvedata")(["X"], pub, shard_count=2)
    assert prov._handle_raw("{not json") == 0
    assert pub.rows == []


# ---- licensed adapters refuse honestly -----------------------------------

@pytest.mark.parametrize("name", ["nyse", "lseg", "nse", "bse"])
def test_licensed_providers_refuse_until_configured(name):
    prov = get_provider(name)([], FakePublisher(), shard_count=2)
    with pytest.raises(ProviderNotConfigured):
        prov.run()


@pytest.mark.parametrize("name", ["finnhub", "twelvedata", "polygon"])
def test_live_ws_providers_need_a_key_to_run(name):
    prov = get_provider(name)(["AAPL"], FakePublisher(), shard_count=2, api_key=None)
    with pytest.raises(ProviderError):
        prov.run()
