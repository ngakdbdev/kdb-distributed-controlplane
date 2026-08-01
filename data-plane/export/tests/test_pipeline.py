"""
Tests for the export pipeline: extractor (fake q conn), the real Parquet sink
roundtrip, job orchestration (fakes), and the sink registry / credentialed
refusals. The Parquet path is exercised for real; the cloud sinks only need to
prove they refuse cleanly without config.
"""
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from export import catalog, get_sink
from export.extractor import KdbExtractor
from export.job import ExportJob
from export.sinks.parquet import ParquetSink
from export.sinks.base import SinkNotConfigured


# a fake q connection: returns a dict-of-columns for any query
class FakeConn:
    def __init__(self, columns):
        self.columns = columns
        self.queries = []

    def __call__(self, query):
        self.queries.append(query)
        return self.columns


class FakeSink:
    def __init__(self):
        self.opened = None
        self.batches = []

    def open(self, schema, table):
        self.opened = (schema, table)

    def write_table(self, batch):
        self.batches.append(batch)
        return batch.num_rows

    def close(self):
        return {"sink": "fake", "batches": len(self.batches)}


# ---- extractor ------------------------------------------------------------

def test_extractor_runs_query_and_yields_arrow():
    conn = FakeConn({"sym": ["AAPL", "MSFT"], "price": [1.0, 2.0]})
    ex = KdbExtractor(conn)
    batches = list(ex.extract("trade", symbols=["AAPL", "MSFT"]))
    assert len(batches) == 1 and batches[0].num_rows == 2
    assert conn.queries == ["select from trade where sym in `AAPL`MSFT"]


def test_extractor_batches_large_results():
    conn = FakeConn({"sym": [f"S{i}" for i in range(10)], "price": list(range(10))})
    ex = KdbExtractor(conn, batch_rows=4)
    batches = list(ex.extract("trade"))
    assert [b.num_rows for b in batches] == [4, 4, 2]


def test_extractor_empty_result_yields_nothing():
    conn = FakeConn({"sym": [], "price": []})
    assert list(KdbExtractor(conn).extract("trade")) == []


# ---- real parquet roundtrip ----------------------------------------------

def test_parquet_sink_writes_readable_file(tmp_path):
    tbl = pa.table({"sym": pa.array(["AAPL", "MSFT"]),
                    "price": pa.array([178.1, 330.5])})
    sink = ParquetSink(out_dir=str(tmp_path))
    sink.open(tbl.schema, "trade")
    n = sink.write_table(tbl)
    stats = sink.close()

    assert n == 2 and stats["rows"] == 2
    back = pq.read_table(stats["path"])
    assert back.num_rows == 2
    assert back.column("sym").to_pylist() == ["AAPL", "MSFT"]


def test_job_extract_to_parquet_end_to_end(tmp_path):
    conn = FakeConn({"sym": ["AAPL", "MSFT", "IBM"], "price": [1.0, 2.0, 3.0]})
    job = ExportJob(KdbExtractor(conn, batch_rows=2), ParquetSink(out_dir=str(tmp_path)))
    report = job.run("trade", symbols=["AAPL", "MSFT", "IBM"])
    assert report.rows == 3
    assert report.batches == 2                       # 2 + 1
    back = pq.read_table(report.target["path"])
    assert back.num_rows == 3


# ---- job orchestration with fakes ----------------------------------------

def test_job_opens_sink_once_with_first_batch_schema():
    conn = FakeConn({"sym": list("abcd"), "price": [1, 2, 3, 4]})
    sink = FakeSink()
    ExportJob(KdbExtractor(conn, batch_rows=2), sink).run("trade")
    assert sink.opened is not None
    assert len(sink.batches) == 2


# ---- registry / credentialed refusals ------------------------------------

def test_catalog_lists_all_sinks_with_offline_flag():
    cat = {s["name"]: s for s in catalog()}
    assert set(cat) == {"parquet", "snowflake", "databricks", "fabric"}
    assert cat["parquet"]["offline"] is True
    assert cat["snowflake"]["offline"] is False
    assert all(cat[n]["requires"] for n in cat)


@pytest.mark.parametrize("name", ["snowflake", "databricks", "fabric"])
def test_cloud_sinks_refuse_without_config(name, monkeypatch):
    # ensure no *_ env leaks in from the host
    for prefix in ("SNOWFLAKE_", "DATABRICKS_", "FABRIC_"):
        for k in list(__import__("os").environ):
            if k.startswith(prefix):
                monkeypatch.delenv(k, raising=False)
    sink = get_sink(name)(config=None)
    with pytest.raises(SinkNotConfigured):
        sink.open(pa.schema([("sym", pa.string())]), "trade")
