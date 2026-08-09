"""
parquet_export.py - build a real Parquet file from a query result grid.

Shared by both export paths in routers/query.py: the synchronous local
download (exports exactly the grid already on screen) and the background
S3/ADLS job (exports a freshly-pulled, possibly much larger grid). Kept as
one module so both agree on exactly how a column's type is inferred and what
"too big for a local download" means - two independent implementations of
this would be exactly the kind of thing that quietly drifts apart.
"""
from __future__ import annotations

import io

import pyarrow as pa
import pyarrow.parquet as pq

# Local (synchronous, browser-download) exports are capped here - past this,
# use the background S3/ADLS path instead, which streams to a temp file and
# uploads rather than holding the whole thing in one HTTP response body.
LOCAL_DOWNLOAD_MAX_BYTES = 10 * 1024 * 1024 * 1024  # 10 GiB


class ExportTooLarge(ValueError):
    def __init__(self, size_bytes: int, limit_bytes: int = LOCAL_DOWNLOAD_MAX_BYTES):
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"result is {size_bytes / (1024**3):.2f} GiB, over the {limit_bytes / (1024**3):.0f} GiB "
            "local-download limit - use the S3/ADLS background export instead")


def build_arrow_table(columns: list[str], rows: list[list]) -> pa.Table:
    """Infer one pyarrow type per column from the JSON values already sent to
    (or fetched for) the browser. Temporal columns arrive as ISO-8601 strings
    (see query_service.py's _jsonable) and are kept as strings here too -
    this exports exactly what was displayed/computed, never a silent
    reinterpretation as a typed timestamp neither side actually agreed on. A
    column with genuinely mixed, arrow-incompatible types (rare) falls back
    to plain text rather than failing the whole export.
    """
    arrays = {}
    for i, col in enumerate(columns):
        values = [row[i] if i < len(row) else None for row in rows]
        try:
            arrays[col] = pa.array(values)
        except Exception:  # noqa: BLE001 - fall back to text, don't fail the export
            arrays[col] = pa.array([None if v is None else str(v) for v in values], type=pa.string())
    return pa.table(arrays)


def write_parquet_bytes(columns: list[str], rows: list[list]) -> bytes:
    """Build + serialize to an in-memory Parquet file. Raises ExportTooLarge
    if the result exceeds LOCAL_DOWNLOAD_MAX_BYTES - call sites decide what to
    do with that (routers/query.py turns it into a 413)."""
    table = build_arrow_table(columns, rows)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    data = buf.getvalue()
    if len(data) > LOCAL_DOWNLOAD_MAX_BYTES:
        raise ExportTooLarge(len(data))
    return data
