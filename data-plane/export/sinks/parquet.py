"""
sinks/parquet.py - write Arrow batches to Parquet. Fully offline and real: this
is the tested path, the common interchange the cloud sinks load from, and a
useful export target in its own right (drop the files on S3/ADLS/GCS and load
them however you like).
"""
from __future__ import annotations

import os

import pyarrow as pa
import pyarrow.parquet as pq

from .base import ExportSink


class ParquetSink(ExportSink):
    name = "parquet"
    display_name = "Parquet files"
    offline = True
    requires = "an output directory (--out); no cloud account needed"

    def __init__(self, out_dir: str, compression: str = "snappy", **_ignored):
        self.out_dir = out_dir
        self.compression = compression
        self._writer = None
        self._path = None
        self._rows = 0
        self._parts = 0

    def open(self, schema: pa.Schema, table: str) -> None:
        os.makedirs(self.out_dir, exist_ok=True)
        self._path = os.path.join(self.out_dir, f"{table}.parquet")
        self._writer = pq.ParquetWriter(self._path, schema, compression=self.compression)

    def write_table(self, batch: pa.Table) -> int:
        self._writer.write_table(batch)
        self._rows += batch.num_rows
        self._parts += 1
        return batch.num_rows

    def close(self) -> dict:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        return {"sink": "parquet", "path": self._path,
                "rows": self._rows, "batches": self._parts}
