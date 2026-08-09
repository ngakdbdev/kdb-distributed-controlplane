"""
export - pull data out of the kdb+ side (HDB history or a live shard's RDB/IDB)
and land it in object storage or a warehouse: S3, ADLS, Snowflake, Databricks,
or Microsoft Fabric.

Design (mirrors the providers framework):

* extractor.py runs a q query against a connection (the gateway for recent data
  across all shards, or a specific shard's RDB/HDB) and yields pyarrow Tables.
* sinks/ are the destinations. Parquet is the common interchange and the one
  that runs fully offline (real + unit-tested). Snowflake/Databricks/Fabric are
  coded to their real SDKs and load that Parquet, but only connect once you plug
  in your account credentials - until then they raise SinkNotConfigured with a
  clear message, they never pretend to have written.
* job.py wires an extractor to a sink and reports rows/batches/target.

The extract -> Parquet path works today with nothing but pyarrow; the warehouse
loads are the credentialed seam.
"""
from .sinks.parquet import ParquetSink
from .sinks.snowflake import SnowflakeSink
from .sinks.databricks import DatabricksSink
from .sinks.fabric import FabricSink
from .sinks.s3 import S3Sink
from .sinks.adls import AdlsSink

_ALL = [ParquetSink, S3Sink, AdlsSink, SnowflakeSink, DatabricksSink, FabricSink]

SINKS = {s.name: s for s in _ALL}


def get_sink(name: str):
    try:
        return SINKS[name]
    except KeyError:
        raise KeyError(f"unknown sink '{name}'. known: {', '.join(sorted(SINKS))}")


def catalog() -> list:
    """Metadata for the UI/CLI: which destinations exist, whether they run
    offline (parquet) or need cloud credentials, and what each requires."""
    return [
        {"name": s.name, "display_name": s.display_name,
         "offline": s.offline, "requires": s.requires}
        for s in _ALL
    ]
