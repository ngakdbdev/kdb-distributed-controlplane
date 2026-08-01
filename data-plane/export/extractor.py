"""
extractor.py - run a q query and yield Arrow batches.

`build_query` (pure, tested) turns an export spec into a q select string for
either HDB history (dated partition) or recent RDB/IDB data. `KdbExtractor`
runs it against an injected connection and converts the result to Arrow, so a
fake connection drives the whole path in tests with no q.
"""
from __future__ import annotations

import logging

from . import schema

log = logging.getLogger("export.extractor")


def _sym_list(symbols: list) -> str:
    # q symbol vector literal: `AAPL`MSFT  (single sym still fine: `AAPL)
    return "".join(f"`{s}" for s in symbols)


def build_query(table: str, date: str | None = None,
                symbols: list | None = None, where: str | None = None) -> str:
    """Build a q select for the export.

    date  -> HDB partition filter date=<d> (q date literal, e.g. 2026.08.01)
    symbols -> sym in (`A`B) style filter (omitted = all symbols)
    where -> extra raw q predicate, appended verbatim (advanced use)
    """
    clauses = []
    if date:
        clauses.append(f"date={date}")
    if symbols:
        if len(symbols) == 1:
            clauses.append(f"sym=`{symbols[0]}")
        else:
            clauses.append(f"sym in {_sym_list(symbols)}")
    if where:
        clauses.append(where)
    if clauses:
        return f"select from {table} where " + ", ".join(clauses)
    return f"select from {table}"


class KdbExtractor:
    """Runs export queries against a q connection and yields Arrow tables.

    `conn` is anything callable as conn(query_string) -> q result (a qpython
    QConnection, or a fake in tests). `batch_rows` slices the result so a large
    HDB pull streams to the sink instead of materializing everything at once.
    """

    def __init__(self, conn, batch_rows: int = 500_000):
        self.conn = conn
        self.batch_rows = batch_rows

    def extract(self, table: str, date: str | None = None,
                symbols: list | None = None, where: str | None = None):
        query = build_query(table, date, symbols, where)
        log.info("extract query: %s", query)
        result = self.conn(query)
        arrow = schema.qtable_to_arrow(result, table_name=table)
        n = arrow.num_rows
        if n == 0:
            log.info("query returned 0 rows")
            return
        for start in range(0, n, self.batch_rows):
            yield arrow.slice(start, self.batch_rows)
