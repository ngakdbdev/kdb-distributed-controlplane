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


def test_route_tiers_no_date_clause_is_rdb_only():
    assert query_router.route_tiers("select from trade where sym=`AAPL") == ["rdb"]


def test_route_tiers_exact_today_is_rdb_only():
    assert query_router.route_tiers("select from trade where date=.z.d") == ["rdb"]
    assert query_router.route_tiers("select from trade where date = .z.D") == ["rdb"]


def test_route_tiers_explicit_date_literal_routes_to_hdb_only():
    # NOT rdb, NOT idb - confirmed live that neither has a `date` column at
    # all (flat undated buffers); only hdb is really date-partitioned.
    q = "select from trade where date=2024.01.15"
    assert query_router.route_tiers(q) == ["hdb"]


def test_route_tiers_within_range_routes_to_hdb_only():
    q = "select from trade where date within (2024.01.01;2024.03.31)"
    assert query_router.route_tiers(q) == ["hdb"]


def test_route_tiers_before_today_routes_to_hdb_even_though_z_d_appears():
    # "date < .z.d" means "everything but today" - pure history, rdb
    # structurally cannot have it (and has no date column to even try). Must
    # NOT be treated as rdb-only just because .z.d appears in the clause.
    q = "select from trade where date<.z.d"
    assert query_router.route_tiers(q) == ["hdb"]


def test_route_tiers_at_or_before_today_routes_to_hdb():
    q = "select from trade where date<=.z.d"
    assert query_router.route_tiers(q) == ["hdb"]


def test_route_tiers_multiple_date_clauses_routes_to_hdb():
    # two separate `date` clauses (e.g. joined query) - can't confidently
    # prove today-only from the "exactly one clause" fast path
    q = "select from trade where date=.z.d, date=.z.d"
    assert query_router.route_tiers(q) == ["hdb"]
