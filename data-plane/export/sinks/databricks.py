"""
sinks/databricks.py - load Arrow batches into a Databricks Delta table.

Path: write each batch to Parquet in a Unity Catalog Volume (or DBFS/cloud
storage staging area), then COPY INTO the target Delta table via a SQL
warehouse using the official databricks-sql-connector. Real, but needs a
workspace + HTTP path + token and the connector installed.

Config (env or dict): DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH,
DATABRICKS_TOKEN, DATABRICKS_CATALOG, DATABRICKS_SCHEMA,
DATABRICKS_VOLUME (a UC volume path for staging, e.g. /Volumes/cat/sch/vol/export).
"""
from __future__ import annotations

import os

import pyarrow as pa
import pyarrow.parquet as pq

from .base import ExportSink, SinkNotConfigured

_REQUIRED = ["server_hostname", "http_path", "token", "catalog", "schema", "volume"]


class DatabricksSink(ExportSink):
    name = "databricks"
    display_name = "Databricks (Delta)"
    offline = False
    requires = "databricks-sql-connector + workspace host/http_path/token + UC catalog/schema/volume"

    def __init__(self, config: dict | None = None, **_ignored):
        self.cfg = self._resolve(config or {})
        self.table = None
        self._conn = None
        self._rows = 0
        self._staged = 0

    @staticmethod
    def _resolve(cfg: dict) -> dict:
        return {k: (cfg.get(k) or os.environ.get(f"DATABRICKS_{k.upper()}")) for k in _REQUIRED}

    def open(self, schema: pa.Schema, table: str) -> None:
        missing = [k for k in _REQUIRED if not self.cfg.get(k)]
        if missing:
            raise SinkNotConfigured(
                "Databricks sink not configured - set " +
                ", ".join(f"DATABRICKS_{k.upper()}" for k in missing) + ".")
        try:
            from databricks import sql  # noqa: F401
        except ImportError as exc:
            raise SinkNotConfigured(
                "databricks-sql-connector is not installed "
                "(pip install databricks-sql-connector).") from exc

        from databricks import sql
        self.table = table
        self._conn = sql.connect(
            server_hostname=self.cfg["server_hostname"],
            http_path=self.cfg["http_path"], access_token=self.cfg["token"])

    def write_table(self, batch: pa.Table) -> int:
        # stage the batch as parquet in the UC volume, then COPY INTO Delta
        stage_name = f"{self.table}_part{self._staged}.parquet"
        stage_path = f"{self.cfg['volume'].rstrip('/')}/{stage_name}"
        local = f"/tmp/{stage_name}"
        pq.write_table(batch, local)
        self._upload_to_volume(local, stage_path)
        target = f"{self.cfg['catalog']}.{self.cfg['schema']}.{self.table}"
        with self._conn.cursor() as cur:
            cur.execute(
                f"COPY INTO {target} FROM '{stage_path}' "
                f"FILEFORMAT = PARQUET COPY_OPTIONS ('mergeSchema' = 'true')")
        self._staged += 1
        self._rows += batch.num_rows
        return batch.num_rows

    def _upload_to_volume(self, local: str, volume_path: str) -> None:
        # Files API upload into the UC volume (databricks-sdk if present)
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient(host=f"https://{self.cfg['server_hostname']}",
                            token=self.cfg["token"])
        with open(local, "rb") as fh:
            w.files.upload(volume_path, fh, overwrite=True)

    def close(self) -> dict:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        return {"sink": "databricks",
                "table": f"{self.cfg.get('catalog')}.{self.cfg.get('schema')}.{self.table}",
                "rows": self._rows}
