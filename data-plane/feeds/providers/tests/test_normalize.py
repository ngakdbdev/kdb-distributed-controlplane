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


def test_coinbase_match_parses_a_trade():
    msg = {"type": "match", "product_id": "BTC-USD", "price": "45000.12",
           "size": "0.015", "side": "sell", "time": "2026-08-11T12:00:00.123456Z"}
    ticks = normalize.coinbase_match(msg)
    assert len(ticks) == 1
    t = ticks[0]
    assert t.symbol == "BTC-USD" and t.price == 45000.12
    assert t.side == "S"                    # taker sold -> aggressor side S
    assert t.size == 0                      # 0.015 rounds to 0 - disclosed precision loss
    assert t.venue == "coinbase"
    assert t.ts == datetime(2026, 8, 11, 12, 0, 0, 123456, tzinfo=timezone.utc)

def test_coinbase_match_rounds_larger_fractional_size():
    msg = {"type": "match", "product_id": "ETH-USD", "price": "3000", "size": "2.6", "side": "buy"}
    ticks = normalize.coinbase_match(msg)
    assert ticks[0].size == 3 and ticks[0].side == "B"

def test_coinbase_match_ignores_non_match_frames():
    assert normalize.coinbase_match({"type": "subscriptions"}) == []
    assert normalize.coinbase_match({"type": "match", "product_id": "BTC-USD"}) == []  # no price
    assert normalize.coinbase_match("not a dict") == []


def test_kraken_trade_parses_batch():
    msg = {"channel": "trade", "type": "update", "data": [
        {"symbol": "BTC/USD", "side": "buy", "price": 45000.5, "qty": 0.02,
         "timestamp": "2026-08-11T12:00:00.000000Z"},
        {"symbol": "ETH/USD", "side": "sell", "price": 3000.0, "qty": 1.5,
         "timestamp": "2026-08-11T12:00:01.000000Z"},
    ]}
    ticks = normalize.kraken_trade(msg)
    assert [t.symbol for t in ticks] == ["BTC/USD", "ETH/USD"]
    assert ticks[0].side == "B" and ticks[1].side == "S"
    assert ticks[0].venue == "kraken"
    assert ticks[1].size == 2  # round(1.5) == 2 (Python's round-half-to-even)

def test_kraken_trade_ignores_non_trade_channels():
    assert normalize.kraken_trade({"channel": "heartbeat"}) == []
    assert normalize.kraken_trade({"channel": "trade", "data": [{"symbol": "X"}]}) == []  # no price
    assert normalize.kraken_trade("not a dict") == []


def test_alpaca_trade_parses_a_batched_array_frame():
    # Alpaca sends an ARRAY per websocket frame, mixing trade/quote/status
    # events together - only "T":"t" ones are trades.
    msg = [
        {"T": "success", "msg": "connected"},
        {"T": "t", "S": "AAPL", "p": 180.23, "s": 100, "t": "2026-08-11T14:30:00.123Z", "x": "V"},
        {"T": "q", "S": "AAPL", "bp": 180.2, "ap": 180.25},   # quote, not a trade - must be ignored
        {"T": "t", "S": "MSFT", "p": 330.5, "s": 250, "t": "2026-08-11T14:30:01.000Z"},
    ]
    ticks = normalize.alpaca_trade(msg)
    assert [t.symbol for t in ticks] == ["AAPL", "MSFT"]
    assert ticks[0].price == 180.23
    assert ticks[0].size == 100
    assert ticks[0].venue == "alpaca:V"
    assert ticks[1].venue == "alpaca"  # no exchange code on this one
    assert ticks[0].ts == datetime(2026, 8, 11, 14, 30, 0, 123000, tzinfo=timezone.utc)


def test_alpaca_trade_accepts_a_single_dict_too():
    msg = {"T": "t", "S": "IBM", "p": 200.0, "s": 10, "t": "2026-08-11T14:30:00.000Z"}
    ticks = normalize.alpaca_trade(msg)
    assert [t.symbol for t in ticks] == ["IBM"]


def test_alpaca_trade_skips_rows_missing_fields():
    msg = [{"T": "t", "S": "AAPL"}, {"T": "t", "p": 10.0}, {"T": "t", "S": "IBM", "p": 5.0}]
    ticks = normalize.alpaca_trade(msg)
    assert [t.symbol for t in ticks] == ["IBM"]


def test_alpaca_trade_ignores_non_trade_events():
    assert normalize.alpaca_trade([{"T": "success", "msg": "authenticated"}]) == []
    assert normalize.alpaca_trade("not a dict or list") == []


def test_ibkr_snapshot_parses_plain_price_and_volume():
    rows = [{"conid": 265598, "31": "180.23", "87": "1234"}]
    ticks = normalize.ibkr_snapshot(rows, {265598: "AAPL"})
    assert ticks[0].symbol == "AAPL"
    assert ticks[0].price == 180.23
    assert ticks[0].size == 1234
    assert ticks[0].venue == "ibkr"


def test_ibkr_snapshot_strips_leading_letter_code_from_price():
    # "C" = last-close price shown when the market isn't currently trading -
    # documented IBKR behavior, not malformed data
    rows = [{"conid": 1, "31": "C180.23"}]
    ticks = normalize.ibkr_snapshot(rows, {1: "AAPL"})
    assert ticks[0].price == 180.23


def test_ibkr_snapshot_parses_abbreviated_volume_suffixes():
    assert normalize.ibkr_snapshot([{"conid": 1, "31": "1.0", "87": "45.2M"}], {1: "X"})[0].size == 45_200_000
    assert normalize.ibkr_snapshot([{"conid": 1, "31": "1.0", "87": "812.3K"}], {1: "X"})[0].size == 812_300
    assert normalize.ibkr_snapshot([{"conid": 1, "31": "1.0", "87": "1.1B"}], {1: "X"})[0].size == 1_100_000_000


def test_ibkr_snapshot_skips_unresolvable_conids():
    rows = [{"conid": 999, "31": "1.0"}]  # not in the conid_to_symbol map
    assert normalize.ibkr_snapshot(rows, {265598: "AAPL"}) == []


def test_ibkr_snapshot_skips_rows_missing_price():
    rows = [{"conid": 1}, {"conid": 2, "31": "not-a-number"}]
    assert normalize.ibkr_snapshot(rows, {1: "A", 2: "B"}) == []


def test_ibkr_snapshot_ignores_non_list_input():
    assert normalize.ibkr_snapshot({"not": "a list"}, {}) == []
    assert normalize.ibkr_snapshot(None, {}) == []
