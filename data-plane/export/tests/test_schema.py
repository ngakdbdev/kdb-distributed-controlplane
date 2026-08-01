"""
Tests for the pure parts of the exporter: kdb->Arrow type mapping, table
building, and the q query builder. No q, no warehouse.
"""
import pyarrow as pa
import pytest

from export import schema
from export.extractor import build_query


# ---- type mapping / arrow schema -----------------------------------------

def test_kdb_types_map_to_expected_arrow():
    assert schema.arrow_type("j") == pa.int64()
    assert schema.arrow_type("f") == pa.float64()
    assert schema.arrow_type("s") == pa.string()
    assert schema.arrow_type("p") == pa.timestamp("ns")
    assert schema.arrow_type("b") == pa.bool_()


def test_unknown_qtype_falls_back_to_string():
    assert schema.arrow_type("Z?") == pa.string()


def test_arrow_schema_for_trade_matches_kdb_schema():
    s = schema.arrow_schema_for("trade")
    assert s.names == ["time", "sym", "price", "size", "side", "venue", "shard"]
    assert s.field("time").type == pa.timestamp("ns")
    assert s.field("price").type == pa.float64()
    assert s.field("size").type == pa.int64()


def test_table_from_columns_builds_typed_table():
    t = schema.table_from_columns(
        ["sym", "price", "size"], ["s", "f", "j"],
        [["AAPL", "MSFT"], [178.1, 330.5], [100, 250]])
    assert t.num_rows == 2
    assert t.schema.field("price").type == pa.float64()
    assert t.column("sym").to_pylist() == ["AAPL", "MSFT"]


def test_table_from_columns_rejects_ragged_input():
    with pytest.raises(ValueError):
        schema.table_from_columns(["a", "b"], ["s"], [["x"]])


def test_qtable_to_arrow_passthrough_and_dict():
    tbl = pa.table({"sym": pa.array(["A"]), "price": pa.array([1.0])})
    assert schema.qtable_to_arrow(tbl) is tbl
    out = schema.qtable_to_arrow({"sym": ["A", "B"], "price": [1.0, 2.0]}, table_name="trade")
    assert out.num_rows == 2 and out.schema.field("price").type == pa.float64()


# ---- query builder --------------------------------------------------------

def test_build_query_all():
    assert build_query("trade") == "select from trade"


def test_build_query_with_date_and_symbols():
    q = build_query("trade", date="2026.08.01", symbols=["AAPL", "MSFT"])
    assert q == "select from trade where date=2026.08.01, sym in `AAPL`MSFT"


def test_build_query_single_symbol_uses_equality():
    assert build_query("trade", symbols=["AAPL"]) == "select from trade where sym=`AAPL"


def test_build_query_appends_raw_where():
    q = build_query("trade", symbols=["AAPL"], where="price>100")
    assert q == "select from trade where sym=`AAPL, price>100"
