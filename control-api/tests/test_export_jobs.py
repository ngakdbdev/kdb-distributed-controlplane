"""Unit tests for export_jobs.py - pure orchestration, driven with fake
fetch_grid callables and monkeypatched sink uploads, same "no q, no cloud
account needed" philosophy as data-plane/export/tests/test_pipeline.py."""
import pytest

from app import export_jobs


@pytest.fixture(autouse=True)
def _clear_jobs():
    export_jobs._JOBS.clear()
    yield
    export_jobs._JOBS.clear()


# ---- gateway pressure check -----------------------------------------------

def test_pressure_flags_high_queue(monkeypatch):
    monkeypatch.setattr(export_jobs.gateway_client, "component_metrics",
                        lambda: [{"shard": "s0", "tpQueue": 999999, "tpSubLag": 0}])
    out = export_jobs.check_gateway_pressure()
    assert out["elevated"] is True
    assert "s0" in out["summary"]


def test_pressure_normal_when_below_threshold(monkeypatch):
    monkeypatch.setattr(export_jobs.gateway_client, "component_metrics",
                        lambda: [{"shard": "s0", "tpQueue": 10, "tpSubLag": 5}])
    out = export_jobs.check_gateway_pressure()
    assert out["elevated"] is False


def test_pressure_check_never_raises_when_metrics_unavailable(monkeypatch):
    def _boom():
        raise RuntimeError("gateway unreachable")
    monkeypatch.setattr(export_jobs.gateway_client, "component_metrics", _boom)
    out = export_jobs.check_gateway_pressure()
    assert out["elevated"] is False
    assert "unavailable" in out["summary"]


# ---- job state machine -----------------------------------------------------

def test_job_succeeds_and_reports_bytes(monkeypatch):
    monkeypatch.setattr(export_jobs, "check_gateway_pressure", lambda: {"elevated": False, "rows": [], "summary": "ok"})
    captured = {}

    def fake_upload(path, bucket, key, region=None, progress_cb=None):
        assert bucket == "my-bucket" and key == "out.parquet"
        if progress_cb:
            progress_cb(50, 100)
            progress_cb(100, 100)
        captured["uploaded"] = True
        return {"sink": "s3", "uri": "s3://my-bucket/out.parquet", "bytes": 100}

    monkeypatch.setattr(export_jobs.export_sinks, "upload_to_s3", fake_upload)

    job = export_jobs.create_job(
        actor="a@b.com", query="select from trade", targets=["gateway"],
        destination={"provider": "s3", "bucket": "my-bucket", "key": "out.parquet"})

    def fetch_grid():
        return ["sym", "price"], [["AAPL", 189.5], ["MSFT", 412.1]]

    export_jobs.run_export_job(job.id, fetch_grid)

    final = export_jobs.get_job(job.id)
    assert final.status == "succeeded"
    assert final.stage == "done"
    assert final.row_count == 2
    assert final.bytes_done == 100 and final.bytes_total == 100
    assert final.result["uri"] == "s3://my-bucket/out.parquet"
    assert captured["uploaded"] is True


def test_job_records_query_failure(monkeypatch):
    monkeypatch.setattr(export_jobs, "check_gateway_pressure", lambda: {"elevated": False, "rows": [], "summary": "ok"})
    job = export_jobs.create_job(actor="a@b.com", query="select from trade", targets=["gateway"],
                                 destination={"provider": "s3", "bucket": "b", "key": "k"})

    def fetch_grid():
        raise RuntimeError("gateway unreachable")

    export_jobs.run_export_job(job.id, fetch_grid)
    final = export_jobs.get_job(job.id)
    assert final.status == "failed"
    assert final.stage == "failed"
    assert "unreachable" in final.error


def test_job_records_sink_not_configured(monkeypatch):
    monkeypatch.setattr(export_jobs, "check_gateway_pressure", lambda: {"elevated": False, "rows": [], "summary": "ok"})
    job = export_jobs.create_job(actor="a@b.com", query="select from trade", targets=["gateway"],
                                 destination={"provider": "adls", "container": "c", "path": "p"})

    def fetch_grid():
        return ["x"], [[1]]

    def fake_upload(*a, **k):
        raise export_jobs.export_sinks.SinkNotConfigured("ADLS not configured - set AZURE_STORAGE_CONNECTION_STRING")

    monkeypatch.setattr(export_jobs.export_sinks, "upload_to_adls", fake_upload)
    export_jobs.run_export_job(job.id, fetch_grid)
    final = export_jobs.get_job(job.id)
    assert final.status == "failed"
    assert "AZURE_STORAGE_CONNECTION_STRING" in final.error


def test_unknown_provider_fails_cleanly(monkeypatch):
    monkeypatch.setattr(export_jobs, "check_gateway_pressure", lambda: {"elevated": False, "rows": [], "summary": "ok"})
    job = export_jobs.create_job(actor="a@b.com", query="select from trade", targets=["gateway"],
                                 destination={"provider": "gcs", "bucket": "b"})

    export_jobs.run_export_job(job.id, lambda: (["x"], [[1]]))
    final = export_jobs.get_job(job.id)
    assert final.status == "failed"
    assert "gcs" in final.error


def test_list_jobs_filters_by_actor_and_sorts_newest_first():
    j1 = export_jobs.create_job(actor="a@b.com", query="q1", targets=["gateway"], destination={"provider": "s3"})
    export_jobs.create_job(actor="other@b.com", query="q2", targets=["gateway"], destination={"provider": "s3"})
    j3 = export_jobs.create_job(actor="a@b.com", query="q3", targets=["gateway"], destination={"provider": "s3"})

    out = export_jobs.list_jobs(actor="a@b.com")
    ids = [j["id"] for j in out]
    assert j1.id in ids and j3.id in ids
    assert all(j["actor"] == "a@b.com" for j in out)
    assert ids.index(j3.id) < ids.index(j1.id)  # newest first
