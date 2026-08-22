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
    assert set(cat) == {"finnhub", "twelvedata", "polygon", "alpaca", "ibkr", "coinbase", "kraken",
                        "binance", "binance-depth", "bybit", "okx",
                        "yahoo", "alphavantage", "nyse", "lseg", "nse", "bse"}
    live = {n for n, p in cat.items() if p["live"]}
    assert live == {"finnhub", "twelvedata", "polygon", "alpaca", "ibkr", "coinbase", "kraken",
                    "binance", "binance-depth", "bybit", "okx", "yahoo", "alphavantage"}
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


def test_alpaca_frame_publishes_sharded_trade_row():
    pub = FakePublisher()
    prov = get_provider("alpaca")(["AAPL"], pub, shard_count=2, api_secret="sekret")
    n = prov._handle_raw('[{"T":"t","S":"AAPL","p":178.1,"s":100,"t":"2026-08-11T14:30:00Z","x":"V"}]')

    assert n == 1
    assert pub.table == "trade"
    row = pub.rows[0]
    assert row[1] == "AAPL" and row[2] == 178.1 and row[3] == 100
    assert row[5] == "alpaca:V"
    assert row[6] == "s0"          # AAPL routes to shard s0 at N=2
    assert len(row) == 7


def test_alpaca_run_refuses_without_secret_key():
    prov = get_provider("alpaca")(["AAPL"], FakePublisher(), shard_count=2, api_key="key-id-only")
    with pytest.raises(ProviderError, match="secret key"):
        prov.run()


def test_polygon_batch_routes_each_symbol_to_its_shard():
    import topology
    pub = FakePublisher()
    prov = get_provider("polygon")(["AAPL", "TSLA"], pub, shard_count=2)
    prov._handle_raw('[{"ev":"T","sym":"AAPL","p":1.0,"s":1,"t":1,"x":11},'
                     ' {"ev":"T","sym":"TSLA","p":2.0,"s":2,"t":2,"x":4}]')
    shards = {row[1]: row[6] for row in pub.rows}
    assert shards["AAPL"] == topology.shard_of("AAPL", 2)
    assert shards["TSLA"] == topology.shard_of("TSLA", 2)


def test_coinbase_frame_publishes_sharded_trade_row():
    pub = FakePublisher()
    prov = get_provider("coinbase")(["BTC-USD"], pub, shard_count=2)
    n = prov._handle_raw('{"type":"match","product_id":"BTC-USD","price":"45000.0",'
                         '"size":"1.5","side":"buy","time":"2026-08-11T12:00:00Z"}')
    assert n == 1
    assert pub.table == "trade"
    row = pub.rows[0]
    assert row[1] == "BTC-USD" and row[2] == 45000.0 and row[4] == "B"
    assert row[5] == "coinbase"

def test_kraken_frame_publishes_sharded_trade_row():
    pub = FakePublisher()
    prov = get_provider("kraken")(["BTC/USD"], pub, shard_count=2)
    n = prov._handle_raw('{"channel":"trade","type":"update","data":[{"symbol":"BTC/USD",'
                         '"side":"sell","price":45000.0,"qty":0.5,"timestamp":"2026-08-11T12:00:00Z"}]}')
    assert n == 1
    row = pub.rows[0]
    assert row[1] == "BTC/USD" and row[4] == "S" and row[5] == "kraken"

def test_binance_frame_publishes_sharded_trade_row():
    pub = FakePublisher()
    prov = get_provider("binance")(["BTCUSDT"], pub, shard_count=2)
    n = prov._handle_raw('{"stream":"btcusdt@trade","data":{"e":"trade","s":"BTCUSDT",'
                         '"p":"45000.0","q":"1.5","T":1701234567000,"m":true}}')
    assert n == 1
    row = pub.rows[0]
    # m=true means the buyer was the maker -> the SELLER was the aggressor -> "S"
    assert row[1] == "BTCUSDT" and row[2] == 45000.0 and row[4] == "S" and row[5] == "binance"


def test_binance_stream_url_lowercases_symbols_for_the_wire():
    prov = get_provider("binance")(["BTCUSDT", "ETHUSDT"], FakePublisher(), shard_count=2)
    url = prov._stream_url()
    assert "btcusdt@trade" in url and "ethusdt@trade" in url


def test_bybit_frame_publishes_sharded_trade_row():
    pub = FakePublisher()
    prov = get_provider("bybit")(["BTCUSDT"], pub, shard_count=2)
    n = prov._handle_raw('{"topic":"publicTrade.BTCUSDT","type":"snapshot","ts":1701234567000,'
                         '"data":[{"T":1701234567000,"s":"BTCUSDT","S":"Sell","v":"0.5","p":"45000.0"}]}')
    assert n == 1
    row = pub.rows[0]
    assert row[1] == "BTCUSDT" and row[2] == 45000.0 and row[4] == "S" and row[5] == "bybit"


def test_okx_frame_publishes_sharded_trade_row():
    pub = FakePublisher()
    prov = get_provider("okx")(["BTC-USDT"], pub, shard_count=2)
    n = prov._handle_raw('{"arg":{"channel":"trades","instId":"BTC-USDT"},'
                         '"data":[{"instId":"BTC-USDT","px":"45000.0","sz":"0.5",'
                         '"side":"buy","ts":"1701234567000"}]}')
    assert n == 1
    row = pub.rows[0]
    assert row[1] == "BTC-USDT" and row[2] == 45000.0 and row[4] == "B" and row[5] == "okx"


def test_crypto_exchanges_dont_need_an_api_key_to_run():
    # unlike finnhub/twelvedata/polygon (see test_live_ws_providers_need_a_key_to_run
    # below) - these are fully public feeds, no ProviderError should fire for a
    # missing key. (Doesn't call run() for real - that would open a real socket;
    # just confirms the constructor accepts api_key=None same as every other call site.)
    for name in ("coinbase", "kraken", "binance", "bybit", "okx"):
        prov = get_provider(name)(["BTC-USD"], FakePublisher(), shard_count=2, api_key=None)
        assert prov.api_key is None


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
