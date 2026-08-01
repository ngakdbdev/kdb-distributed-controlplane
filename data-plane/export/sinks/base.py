"""
sinks/base.py - the destination interface + shared errors.

An ExportSink is opened with the Arrow schema, fed Arrow tables batch by batch,
and closed. Concrete sinks: parquet (offline, real), snowflake/databricks/fabric
(credentialed).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pyarrow as pa


class SinkError(RuntimeError):
    pass


class SinkNotConfigured(SinkError):
    """A credentialed sink with no account config / missing client library.
    Carries a message pointing at exactly what to set."""


class ExportSink(ABC):
    # catalog metadata (subclasses override)
    name = "base"
    display_name = "Base"
    offline = False          # True: runs with no cloud account (parquet)
    requires = ""

    @abstractmethod
    def open(self, schema: pa.Schema, table: str) -> None:
        ...

    @abstractmethod
    def write_table(self, batch: pa.Table) -> int:
        """Write one Arrow batch; return rows written."""
        ...

    @abstractmethod
    def close(self) -> dict:
        """Finish and return a small stats/target summary dict."""
        ...
