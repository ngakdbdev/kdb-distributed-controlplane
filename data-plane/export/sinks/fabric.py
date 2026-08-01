"""
sinks/fabric.py - land Arrow batches in a Microsoft Fabric Lakehouse (OneLake).

Path: write each batch to Parquet directly into the Lakehouse's Files area in
OneLake over the ADLS Gen2 / abfss endpoint, authenticated with an Entra
service principal (azure-identity + azure-storage-file-datalake). Fabric then
loads/shortcuts those Parquet files into a Delta table. Real, but needs a Fabric
workspace + Lakehouse + service-principal credentials and the azure libraries.

Config (env or dict): FABRIC_WORKSPACE, FABRIC_LAKEHOUSE, FABRIC_TENANT_ID,
FABRIC_CLIENT_ID, FABRIC_CLIENT_SECRET. OneLake DFS host is onelake.dfs.fabric.microsoft.com.
"""
from __future__ import annotations

import io
import os

import pyarrow as pa
import pyarrow.parquet as pq

from .base import ExportSink, SinkNotConfigured

_REQUIRED = ["workspace", "lakehouse", "tenant_id", "client_id", "client_secret"]
_ONELAKE_HOST = "onelake.dfs.fabric.microsoft.com"


class FabricSink(ExportSink):
    name = "fabric"
    display_name = "Microsoft Fabric (OneLake Lakehouse)"
    offline = False
    requires = "azure-identity + azure-storage-file-datalake + Fabric workspace/lakehouse + service principal"

    def __init__(self, config: dict | None = None, **_ignored):
        self.cfg = self._resolve(config or {})
        self.table = None
        self._fs = None
        self._rows = 0
        self._staged = 0

    @staticmethod
    def _resolve(cfg: dict) -> dict:
        return {k: (cfg.get(k) or os.environ.get(f"FABRIC_{k.upper()}")) for k in _REQUIRED}

    def open(self, schema: pa.Schema, table: str) -> None:
        missing = [k for k in _REQUIRED if not self.cfg.get(k)]
        if missing:
            raise SinkNotConfigured(
                "Fabric sink not configured - set " +
                ", ".join(f"FABRIC_{k.upper()}" for k in missing) + ".")
        try:
            from azure.identity import ClientSecretCredential  # noqa: F401
            from azure.storage.filedatalake import DataLakeServiceClient  # noqa: F401
        except ImportError as exc:
            raise SinkNotConfigured(
                "azure libraries not installed (pip install azure-identity "
                "azure-storage-file-datalake).") from exc

        from azure.identity import ClientSecretCredential
        from azure.storage.filedatalake import DataLakeServiceClient
        self.table = table
        cred = ClientSecretCredential(
            tenant_id=self.cfg["tenant_id"], client_id=self.cfg["client_id"],
            client_secret=self.cfg["client_secret"])
        service = DataLakeServiceClient(
            account_url=f"https://{_ONELAKE_HOST}", credential=cred)
        # in OneLake the workspace is the filesystem, the lakehouse a directory
        self._fs = service.get_file_system_client(self.cfg["workspace"])

    def write_table(self, batch: pa.Table) -> int:
        buf = io.BytesIO()
        pq.write_table(batch, buf)
        buf.seek(0)
        # .../<lakehouse>.Lakehouse/Files/export/<table>/part-N.parquet
        path = (f"{self.cfg['lakehouse']}.Lakehouse/Files/export/"
                f"{self.table}/part-{self._staged}.parquet")
        file_client = self._fs.get_file_client(path)
        data = buf.getvalue()
        file_client.upload_data(data, overwrite=True)
        self._staged += 1
        self._rows += batch.num_rows
        return batch.num_rows

    def close(self) -> dict:
        self._fs = None
        return {"sink": "fabric", "lakehouse": self.cfg.get("lakehouse"),
                "table": self.table, "rows": self._rows,
                "note": "Parquet written to OneLake Files; load/shortcut into a Delta table in Fabric"}
