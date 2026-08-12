"""Tests for the "subscribe to everything" path: each crypto provider's
fetch_all_symbols() (a real REST call to the venue's own instrument list,
here driven with canned JSON matching each venue's actual response shape -
no network) and the base.chunked() helper that splits a symbol list to
respect a venue's per-connection/per-message subscription limit."""
from providers import get_provider
from providers.base import chunked


def test_chunked_splits_preserving_order():
    assert list(chunked([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_chunked_single_chunk_when_under_size():
    assert list(chunked([1, 2, 3], 10)) == [[1, 2, 3]]


def test_chunked_empty_list():
    assert list(chunked([], 10)) == []


def test_binance_fetch_all_symbols_filters_trading_only():
    canned = {"symbols": [
        {"symbol": "BTCUSDT", "status": "TRADING"},
        {"symbol": "ETHUSDT", "status": "TRADING"},
        {"symbol": "DELISTEDCOIN", "status": "BREAK"},
    ]}
    syms = get_provider("binance").fetch_all_symbols(fetch=lambda url: canned)
    assert syms == ["BTCUSDT", "ETHUSDT"]


def test_binance_stream_url_accepts_an_explicit_chunk():
    prov = get_provider("binance")(["BTCUSDT", "ETHUSDT", "SOLUSDT"], None, shard_count=2)
    url = prov._stream_url(["BTCUSDT"])
    assert "btcusdt@trade" in url and "ethusdt@trade" not in url


def test_bybit_fetch_all_symbols_filters_trading_only():
    canned = {"result": {"list": [
        {"symbol": "BTCUSDT", "status": "Trading"},
        {"symbol": "ETHUSDT", "status": "Trading"},
        {"symbol": "DELISTED", "status": "Closed"},
    ]}}
    syms = get_provider("bybit").fetch_all_symbols(fetch=lambda url: canned)
    assert syms == ["BTCUSDT", "ETHUSDT"]


def test_okx_fetch_all_symbols_filters_live_only():
    canned = {"data": [
        {"instId": "BTC-USDT", "state": "live"},
        {"instId": "ETH-USDT", "state": "live"},
        {"instId": "OLD-USDT", "state": "suspend"},
    ]}
    syms = get_provider("okx").fetch_all_symbols(fetch=lambda url: canned)
    assert syms == ["BTC-USDT", "ETH-USDT"]


def test_kraken_fetch_all_symbols_uses_wsname_not_the_dict_key():
    # the REST endpoint's dict keys (XXBTZUSD) are internal asset codes, NOT
    # usable on the v2 websocket - wsname is the field that is
    canned = {"result": {
        "XXBTZUSD": {"wsname": "XBT/USD"},
        "XETHZUSD": {"wsname": "ETH/USD"},
        "SOMEFUTURE": {},  # no wsname - some pairs have none, must be skipped
    }}
    syms = get_provider("kraken").fetch_all_symbols(fetch=lambda url: canned)
    assert syms == ["ETH/USD", "XBT/USD"]


def test_coinbase_fetch_all_symbols_filters_online_only():
    canned = [
        {"id": "BTC-USD", "status": "online"},
        {"id": "ETH-USD", "status": "online"},
        {"id": "OLD-USD", "status": "delisted"},
    ]
    syms = get_provider("coinbase").fetch_all_symbols(fetch=lambda url: canned)
    assert syms == ["BTC-USD", "ETH-USD"]


def test_binance_run_shards_across_multiple_connections_past_the_cap(monkeypatch):
    """Binance's combined-stream URL caps a single connection at 1024
    streams - past that, run() must open MULTIPLE connections (one per
    STREAM_CHUNK-sized group), not silently truncate the symbol list."""
    import sys
    import types

    prov = get_provider("binance")(["A", "B", "C", "D", "E"], None, shard_count=2)
    prov.STREAM_CHUNK = 2  # force 3 connections for 5 symbols: [A,B] [C,D] [E]

    created_urls = []

    class FakeWebSocketApp:
        def __init__(self, url, on_open=None, on_message=None, on_error=None):
            created_urls.append(url)

        def run_forever(self, reconnect=None):
            pass  # each "connection" returns immediately - no real network, no real blocking

    fake_module = types.SimpleNamespace(WebSocketApp=FakeWebSocketApp)
    monkeypatch.setitem(sys.modules, "websocket", fake_module)

    prov.run()  # joins all connection threads before returning

    assert len(created_urls) == 3
    all_streams = {s for url in created_urls for s in url.split("streams=")[1].split("/")}
    assert all_streams == {"a@trade", "b@trade", "c@trade", "d@trade", "e@trade"}


def test_binance_run_single_connection_when_under_the_cap(monkeypatch):
    import sys
    import types

    prov = get_provider("binance")(["A", "B"], None, shard_count=2)
    created_urls = []

    class FakeWebSocketApp:
        def __init__(self, url, on_open=None, on_message=None, on_error=None):
            created_urls.append(url)

        def run_forever(self, reconnect=None):
            pass

    fake_module = types.SimpleNamespace(WebSocketApp=FakeWebSocketApp)
    monkeypatch.setitem(sys.modules, "websocket", fake_module)

    prov.run()

    assert len(created_urls) == 1


def test_binance_depth_frame_publishes_bid_and_ask_rows():
    pub_rows = []

    class FakePublisher:
        def publish_rows(self, table, rows):
            self.table = table
            pub_rows.extend(rows)

    prov = get_provider("binance-depth")(["BTCUSDT"], FakePublisher(), shard_count=2)
    raw = ('{"stream":"btcusdt@depth@100ms","data":{"e":"depthUpdate","s":"BTCUSDT",'
          '"E":1701234567000,"b":[["45000.00","1.5"],["44999.00","0"]],'
          '"a":[["45001.00","0.8"]]}}')
    n = prov._handle_raw(raw)

    assert n == 3  # 2 bid levels (one a removal) + 1 ask level
    assert 45000.0 in [r[2] for r in pub_rows]
    bid_removed = next(r for r in pub_rows if r[2] == 44999.0)
    assert bid_removed[3] == 0 and bid_removed[4] == "BID"  # removed level -> size 0, still published
    ask = next(r for r in pub_rows if r[4] == "ASK")
    assert ask[2] == 45001.0 and ask[3] == 1  # round(0.8) == 1 via _crypto_size
    assert all(r[5] == "binance-depth" for r in pub_rows)  # never plain "binance"


def test_binance_depth_ignores_non_depth_frames():
    prov = get_provider("binance-depth")(["BTCUSDT"], None, shard_count=2)
    assert prov._handle_raw('{"stream":"btcusdt@trade","data":{"e":"trade"}}') == 0


def test_bybit_run_subscribes_in_batches_on_one_connection(monkeypatch):
    """SUBSCRIBE_CHUNK symbols per subscribe frame, several frames on the
    SAME connection - not one giant message, and not multiple sockets. Drives
    the REAL run()/on_open code by faking out the websocket lib entirely."""
    import json
    import sys
    import types

    prov = get_provider("bybit")(["S1", "S2", "S3"], None, shard_count=2)
    prov.SUBSCRIBE_CHUNK = 2

    sent = []
    created = []

    class FakeWS:
        def send(self, msg):
            sent.append(msg)

    class FakeWebSocketApp:
        def __init__(self, url, on_open=None, on_message=None, on_error=None):
            created.append(url)
            self._on_open = on_open

        def run_forever(self, reconnect=None):
            self._on_open(FakeWS())  # simulate the socket opening, no real network

    fake_module = types.SimpleNamespace(WebSocketApp=FakeWebSocketApp)
    monkeypatch.setitem(sys.modules, "websocket", fake_module)

    prov.run()

    assert len(created) == 1                 # ONE connection, not one per batch
    assert len(sent) == 2                     # ceil(3/2) subscribe frames
    all_args = [a for msg in sent for a in json.loads(msg)["args"]]
    assert sorted(all_args) == ["publicTrade.S1", "publicTrade.S2", "publicTrade.S3"]
