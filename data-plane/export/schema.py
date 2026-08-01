"""
schema.py - map kdb+/q column types to Arrow, and build Arrow tables.

The q->Arrow type mapping and the pure `table_from_columns` builder are what's
unit-tested; `qtable_to_arrow` is the thin adapter over an actual qpython
result (numpy arrays), exercised only against a live q.
"""
from __future__ import annotations

import pyarrow as pa

# q type char -> pyarrow type. Covers the types in the trade/risk schemas plus
# the common ones you'd hit exporting arbitrary tables.
KDB_TYPE_TO_ARROW = {
    "b": pa.bool_(),            # boolean
    "x": pa.uint8(),           # byte
    "h": pa.int16(),           # short
    "i": pa.int32(),           # int
    "j": pa.int64(),           # long
    "e": pa.float32(),         # real
    "f": pa.float64(),         # float
    "c": pa.string(),          # char
    "s": pa.string(),          # symbol
    "p": pa.timestamp("ns"),   # timestamp
    "d": pa.date32(),          # date
    "n": pa.duration("ns"),    # timespan
    "t": pa.time64("ns"),      # time
    "z": pa.timestamp("ms"),   # datetime
}

# the shipped table schemas (mirror data-plane/q/schema.q), as (name, qtype)
TABLE_SCHEMAS = {
    "trade": [("time", "p"), ("sym", "s"), ("price", "f"), ("size", "j"),
              ("side", "s"), ("venue", "s"), ("shard", "s")],
    "risk": [("time", "p"), ("sym", "s"), ("riskType", "s"), ("limit", "f"),
             ("exposure", "f"), ("status", "s"), ("shard", "s")],
}


def arrow_type(qtype: str) -> pa.DataType:
    try:
        return KDB_TYPE_TO_ARROW[qtype]
    except KeyError:
        # unknown/unsupported q type -> fall back to string so an export never
        # hard-fails on an exotic column; the value is stringified upstream.
        return pa.string()


def arrow_schema_for(table: str) -> pa.Schema:
    """Arrow schema for a known table name (trade/risk)."""
    cols = TABLE_SCHEMAS[table]
    return pa.schema([(name, arrow_type(qt)) for name, qt in cols])


def table_from_columns(names: list, qtypes: list, columns: list) -> pa.Table:
    """Build a pyarrow Table from parallel column data. Pure - the tested core.

    names/qtypes/columns are parallel lists; columns[i] is the list of values
    for column names[i], typed per qtypes[i].
    """
    if not (len(names) == len(qtypes) == len(columns)):
        raise ValueError("names, qtypes, columns must be the same length")
    arrays = [pa.array(col, type=arrow_type(qt)) for col, qt in zip(columns, qtypes)]
    return pa.table(dict(zip(names, arrays)),
                    schema=pa.schema([(n, arrow_type(qt)) for n, qt in zip(names, qtypes)]))


def qtable_to_arrow(qtable, table_name: str | None = None) -> pa.Table:
    """Convert a qpython result to an Arrow table.

    Accepts (for testability) a few shapes:
      - a pyarrow.Table -> returned as-is
      - a dict {colname: list-of-values} -> inferred/known types
      - otherwise a qpython QTable (numpy columns) -> converted lazily
    """
    if isinstance(qtable, pa.Table):
        return qtable
    if isinstance(qtable, dict):
        names = list(qtable.keys())
        known = dict(TABLE_SCHEMAS.get(table_name or "", []))
        qtypes = [known.get(n, "s") for n in names]  # default symbol/string
        # let pyarrow infer when we don't know the type, else use the mapping
        cols = [qtable[n] for n in names]
        try:
            return table_from_columns(names, qtypes, cols)
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            return pa.table({n: pa.array(qtable[n]) for n in names})
    # qpython QTable: it behaves like a numpy structured array with dtype.names
    import numpy as np  # noqa: F401 - qpython returns numpy arrays
    names = list(qtable.dtype.names)
    return pa.table({n: pa.array(qtable[n]) for n in names})
