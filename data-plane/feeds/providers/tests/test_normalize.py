"""
Tests for providers.normalize - the pure vendor-payload -> Tick parsers. These
are the bits that must exactly match each vendor's websocket format, so they're
pinned here with representative sample frames. No sockets, no topology.
"""
from datetime import datetime, timezone

from providers import normalize


def test_finnhub_trades_parses_batch():
    msg = {"type": "trade", "data": [
        {"s": "AAPL", "p": 178.12, "v": 100, "t": 1701234567000},
        {"s": "MSFT", "p": 330.5, "v": 250, "t": 1701234567500},
    ]}
    ticks = normalize.finnhub_trades(msg)
    assert [t.symbol for t in ticks] == ["AAPL", "MSFT"]
    assert ticks[0].price == 178.12
    assert ticks[0].size == 100
    assert ticks[0].venue == "finnhub"
    assert ticks[0].ts == datetime.fromtimestamp(1701234567.0, tz=timezone.utc)


def test_finnhub_ignores_non_trade_frames():
    assert normalize.finnhub_trades({"type": "ping"}) == []
    assert normalize.finnhub_trades({"type": "trade"}) == []      # no data
    assert normalize.finnhub_trades("not a dict") == []


def test_finnhub_skips_rows_missing_fields():
    msg = {"type": "trade", "data": [{"s": "AAPL"}, {"p": 10.0}, {"s": "IBM", "p": 5.0}]}
    ticks = normalize.finnhub_trades(msg)
    assert [t.symbol for t in ticks] == ["IBM"]


def test_twelvedata_price_parses_single():
    msg = {"event": "price", "symbol": "RELIANCE", "price": 2900.5,
           "exchange": "NSE", "timestamp": 1701234567, "day_volume": 12345}
    ticks = normalize.twelvedata_price(msg)
    assert len(ticks) == 1
    t = ticks[0]
    assert t.symbol == "RELIANCE"
    assert t.price == 2900.5
    assert t.venue == "NSE"
    assert t.size == 12345
    assert t.ts == datetime.fromtimestamp(1701234567, tz=timezone.utc)


def test_twelvedata_ignores_subscribe_acks():
    assert normalize.twelvedata_price({"event": "subscribe-status", "status": "ok"}) == []
    assert normalize.twelvedata_price({"event": "price", "symbol": "X"}) == []   # no price


def test_polygon_parses_trade_events_and_skips_others():
    msg = [
        {"ev": "status", "status": "connected"},
        {"ev": "T", "sym": "AAPL", "p": 178.1, "s": 100, "t": 1701234567000, "x": 11},
        {"ev": "Q", "sym": "AAPL"},   # quote, not a trade
        {"ev": "T", "sym": "TSLA", "p": 240.0, "s": 5, "t": 1701234567001, "x": 4},
    ]
    ticks = normalize.polygon_messages(msg)
    assert [t.symbol for t in ticks] == ["AAPL", "TSLA"]
    assert ticks[0].venue == "11"       # exchange id stringified
    assert ticks[0].size == 100


def test_polygon_accepts_single_object_too():
    ticks = normalize.polygon_messages({"ev": "T", "sym": "IBM", "p": 140.0, "s": 10, "t": 1})
    assert len(ticks) == 1 and ticks[0].symbol == "IBM"


def test_yahoo_quotes_parses_result_list():
    data = {"quoteResponse": {"result": [
        {"symbol": "AAPL", "regularMarketPrice": 178.1, "regularMarketVolume": 1000,
         "fullExchangeName": "NasdaqGS", "regularMarketTime": 1701234567},
    ]}}
    ticks = normalize.yahoo_quotes(data)
    assert len(ticks) == 1
    assert ticks[0].symbol == "AAPL" and ticks[0].price == 178.1
    assert ticks[0].venue == "NasdaqGS" and ticks[0].size == 1000


def test_yahoo_quotes_handles_empty_and_missing_fields():
    assert normalize.yahoo_quotes({"quoteResponse": {"result": []}}) == []
    assert normalize.yahoo_quotes({}) == []
    # a result row missing price is skipped
    assert normalize.yahoo_quotes({"quoteResponse": {"result": [{"symbol": "X"}]}}) == []


def test_alphavantage_quote_parses_global_quote():
    data = {"Global Quote": {"01. symbol": "IBM", "05. price": "140.25", "06. volume": "123"}}
    ticks = normalize.alphavantage_quote(data)
    assert len(ticks) == 1
    assert ticks[0].symbol == "IBM" and ticks[0].price == 140.25 and ticks[0].size == 123


def test_alphavantage_quote_handles_rate_limit_note():
    # AV returns a {"Note": "...rate limit..."} with no Global Quote when throttled
    assert normalize.alphavantage_quote({"Note": "call frequency exceeded"}) == []
    assert normalize.alphavantage_quote({"Global Quote": {}}) == []
