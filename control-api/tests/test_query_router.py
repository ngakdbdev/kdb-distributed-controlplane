"""Tests for query_router.py's best-effort symbol extraction / shard routing,
including the `$"..."` cast form (the only safe way to embed a symbol with a
hyphen or slash in it - see tradingCore.js's qSym / signal_engine.py's
fetch_trade_tape for the bug this exists to avoid regressing)."""
from app import query_router, topology


def test_extract_syms_bare_backtick_token():
    assert query_router.extract_syms("select from trade where sym=`AAPL") == ["AAPL"]


def test_extract_syms_bare_backtick_list():
    assert query_router.extract_syms("select from trade where sym in `AAPL`MSFT") == ["AAPL", "MSFT"]


def test_extract_syms_cast_form_single():
    assert query_router.extract_syms('select from trade where sym=`$"ETH-USD"') == ["ETH-USD"]


def test_extract_syms_cast_form_list():
    q = 'select from trade where sym in (`$"BTC/USD";`$"ETH-USD")'
    assert query_router.extract_syms(q) == ["BTC/USD", "ETH-USD"]


def test_extract_syms_mixed_forms_in_one_clause():
    q = 'select from trade where sym in (`AAPL;`$"ETH-USD")'
    assert query_router.extract_syms(q) == ["AAPL", "ETH-USD"]


def test_extract_syms_no_sym_filter_returns_none():
    assert query_router.extract_syms("select from trade") is None


def test_route_shards_narrows_to_owning_shards():
    shards = query_router.route_shards('select from trade where sym=`$"ETH-USD"', 2)
    assert shards == [topology.shard_of("ETH-USD", 2)]


def test_route_shards_none_when_unrecognized():
    assert query_router.route_shards("select from trade", 2) is None
