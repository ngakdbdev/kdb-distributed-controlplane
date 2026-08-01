"""
sinks/snowflake.py - load Arrow batches into a Snowflake table.

Uses the official snowflake-connector-python with write_pandas (Arrow -> pandas
-> bulk load via a temporary internal stage + COPY INTO), which is the standard
fast path. Real, but needs a Snowflake account + credentials and the connector
installed - until then it raises SinkNotConfigured rather than pretending.

Config (env or passed dict): SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER,
SNOWFLAKE_PASSWORD (or key-pair), SNOWFLAKE_WAREHOUSE, SNOWFLAKE_DATABASE,
SNOWFLAKE_SCHEMA.
"""
from __future__ import annotations

import os

import pyarrow as pa

from .base import ExportSink, SinkNotConfigured, SinkError

_REQUIRED = ["account", "user", "warehouse", "database", "schema"]


class SnowflakeSink(ExportSink):
    name = "snowflake"
    display_name = "Snowflake"
    offline = False
    requires = "snowflake-connector-python + account/user/password/warehouse/database/schema"

    def __init__(self, config: dict | None = None, **_ignored):
        self.cfg = self._resolve(config or {})
        self.table = None
        self._conn = None
        self._rows = 0

    @staticmethod
    def _resolve(cfg: dict) -> dict:
        def g(k):
            return cfg.get(k) or os.environ.get(f"SNOWFLAKE_{k.upper()}")
        out = {k: g(k) for k in _REQUIRED}
        out["password"] = g("password")
        return out

    def open(self, schema: pa.Schema, table: str) -> None:
        missing = [k for k in _REQUIRED if not self.cfg.get(k)]
        if missing:
            raise SinkNotConfigured(
                "Snowflake sink not configured - set " +
                ", ".join(f"SNOWFLAKE_{k.upper()}" for k in missing) +
                " (and SNOWFLAKE_PASSWORD or key-pair).")
        try:
            import snowflake.connector  # noqa: F401
        except ImportError as exc:
            raise SinkNotConfigured(
                "snowflake-connector-python is not installed (pip install "
                "'snowflake-connector-python[pandas]').") from exc

        import snowflake.connector as sf
        self.table = table.upper()
        self._conn = sf.connect(
            account=self.cfg["account"], user=self.cfg["user"],
            password=self.cfg.get("password"), warehouse=self.cfg["warehouse"],
            database=self.cfg["database"], schema=self.cfg["schema"])

    def write_table(self, batch: pa.Table) -> int:
        from snowflake.connector.pandas_tools import write_pandas
        df = batch.to_pandas()
        success, _nchunks, nrows, _ = write_pandas(
            self._conn, df, self.table, auto_create_table=True, quote_identifiers=False)
        if not success:
            raise SinkError("snowflake write_pandas reported failure")
        self._rows += nrows
        return nrows

    def close(self) -> dict:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        return {"sink": "snowflake", "table": self.table,
                "database": self.cfg.get("database"), "rows": self._rows}
