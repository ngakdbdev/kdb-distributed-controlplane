"""
sinks/adls.py - upload the exported Parquet file to Azure Data Lake Storage
Gen2, over its blob endpoint. ADLS Gen2 is blob storage with a hierarchical
namespace, so the plain azure-storage-blob client works fine for a
single-file upload like this one - you only need the separate
azure-storage-file-datalake client (as fabric.py uses) if you need
directory-level ACLs, which this sink doesn't.

Same buffer-locally-then-upload-once shape as sinks/s3.py, with real
byte-level progress via azure-storage-blob's upload_blob progress_hook
(added in azure-storage-blob 12.x - verify against your installed SDK
version before relying on it in front of anyone, same as this repo's other
"real but not exercised against a live account in this sandbox" seams).

Auth: AZURE_STORAGE_CONNECTION_STRING (simplest), or ADLS_ACCOUNT_URL +
DefaultAzureCredential (managed identity / az login / service-principal env
vars) if no connection string is set. Never a credential entered as config -
only the destination (container + blob path) is per-request.
"""
from __future__ import annotations

import os
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

from .base import ExportSink, SinkError, SinkNotConfigured

_REQUIRED = ["container", "path"]


class AdlsSink(ExportSink):
    name = "adls"
    display_name = "Azure Data Lake Storage"
    offline = False
    requires = ("azure-storage-blob + a container/path destination; "
                "AZURE_STORAGE_CONNECTION_STRING or ADLS_ACCOUNT_URL + DefaultAzureCredential")

    def __init__(self, config: dict | None = None, progress_cb=None, **_ignored):
        cfg = config or {}
        self.cfg = {"container": cfg.get("container") or os.environ.get("ADLS_CONTAINER"),
                    "path": cfg.get("path")}
        self.account_url = os.environ.get("ADLS_ACCOUNT_URL")
        self.connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        self.progress_cb = progress_cb  # optional callable(bytes_done, bytes_total)
        self._writer = None
        self._tmp_path = None
        self._rows = 0

    def open(self, schema: pa.Schema, table: str) -> None:
        missing = [k for k in _REQUIRED if not self.cfg.get(k)]
        if missing:
            raise SinkNotConfigured(
                "ADLS sink not configured - missing " + ", ".join(missing) +
                " (container can default from ADLS_CONTAINER; path is always required per export).")
        if not self.connection_string and not self.account_url:
            raise SinkNotConfigured(
                "ADLS sink not configured - set AZURE_STORAGE_CONNECTION_STRING, "
                "or ADLS_ACCOUNT_URL to authenticate via DefaultAzureCredential.")
        try:
            from azure.storage.blob import BlobServiceClient  # noqa: F401
        except ImportError as exc:
            raise SinkNotConfigured("azure-storage-blob is not installed (pip install azure-storage-blob).") from exc

        fd, self._tmp_path = tempfile.mkstemp(suffix=".parquet")
        os.close(fd)
        self._writer = pq.ParquetWriter(self._tmp_path, schema, compression="snappy")

    def write_table(self, batch: pa.Table) -> int:
        self._writer.write_table(batch)
        self._rows += batch.num_rows
        return batch.num_rows

    def close(self) -> dict:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        if not self._tmp_path:
            return {"sink": "adls", "rows": 0}

        from azure.storage.blob import BlobServiceClient
        if self.connection_string:
            service = BlobServiceClient.from_connection_string(self.connection_string)
        else:
            from azure.identity import DefaultAzureCredential
            service = BlobServiceClient(account_url=self.account_url, credential=DefaultAzureCredential())

        blob_client = service.get_blob_client(container=self.cfg["container"], blob=self.cfg["path"])
        size = os.path.getsize(self._tmp_path)

        def _progress(current, total):
            if self.progress_cb:
                self.progress_cb(current, total or size)

        try:
            with open(self._tmp_path, "rb") as fh:
                blob_client.upload_blob(fh, overwrite=True, progress_hook=_progress)
        except Exception as exc:  # noqa: BLE001
            raise SinkError(f"ADLS upload failed: {exc}") from exc
        finally:
            os.remove(self._tmp_path)
            self._tmp_path = None

        return {"sink": "adls", "uri": f"{self.cfg['container']}/{self.cfg['path']}",
                "container": self.cfg["container"], "path": self.cfg["path"],
                "rows": self._rows, "bytes": size}
