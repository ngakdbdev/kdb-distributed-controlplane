"""
sinks/s3.py - upload the exported Parquet file to an S3 bucket/key.

Unlike the warehouse sinks (Snowflake/Databricks/Fabric), S3 has no notion of
a target "table" - it's a single Parquet object at a bucket/key. Batches are
buffered to a local temp Parquet file as they arrive (same as parquet.py),
then uploaded as one object in close() via boto3's managed transfer, which
reports real byte-level progress through a Callback - the one place in this
path where "progress" is an actual transferred-bytes count, not an estimate.

Credentials are never taken from config/UI input - boto3 resolves them from
its standard chain (env AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_SESSION_TOKEN,
an attached IAM role, or ~/.aws/credentials). Config here is only the
destination: bucket + key + optional region.
"""
from __future__ import annotations

import os
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

from .base import ExportSink, SinkError, SinkNotConfigured

_REQUIRED = ["bucket", "key"]


class S3Sink(ExportSink):
    name = "s3"
    display_name = "Amazon S3"
    offline = False
    requires = ("boto3 + a bucket/key destination; AWS credentials via the standard "
                "boto3 chain (env / IAM role / ~/.aws), never entered as config")

    def __init__(self, config: dict | None = None, progress_cb=None, **_ignored):
        cfg = config or {}
        self.cfg = {"bucket": cfg.get("bucket") or os.environ.get("S3_BUCKET"), "key": cfg.get("key")}
        self.region = cfg.get("region") or os.environ.get("S3_REGION")
        self.progress_cb = progress_cb  # optional callable(bytes_done, bytes_total)
        self._writer = None
        self._tmp_path = None
        self._rows = 0

    def open(self, schema: pa.Schema, table: str) -> None:
        missing = [k for k in _REQUIRED if not self.cfg.get(k)]
        if missing:
            raise SinkNotConfigured(
                "S3 sink not configured - missing " + ", ".join(missing) +
                " (bucket can default from S3_BUCKET; key is always required per export).")
        try:
            import boto3  # noqa: F401
        except ImportError as exc:
            raise SinkNotConfigured("boto3 is not installed (pip install boto3).") from exc

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
            return {"sink": "s3", "rows": 0}

        import boto3
        client = boto3.client("s3", region_name=self.region) if self.region else boto3.client("s3")
        size = os.path.getsize(self._tmp_path)
        done = {"bytes": 0}

        def _cb(chunk_bytes):
            done["bytes"] += chunk_bytes
            if self.progress_cb:
                self.progress_cb(done["bytes"], size)

        try:
            client.upload_file(self._tmp_path, self.cfg["bucket"], self.cfg["key"], Callback=_cb)
        except Exception as exc:  # noqa: BLE001 - never pretend it uploaded
            raise SinkError(f"S3 upload failed: {exc}") from exc
        finally:
            os.remove(self._tmp_path)
            self._tmp_path = None

        return {"sink": "s3", "uri": f"s3://{self.cfg['bucket']}/{self.cfg['key']}",
                "bucket": self.cfg["bucket"], "key": self.cfg["key"], "rows": self._rows, "bytes": size}
