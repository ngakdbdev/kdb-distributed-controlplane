"""
export_jobs.py - background query-export jobs (to S3 or ADLS) with real
progress, plus a check of whether the gateway looks overloaded before a big
pull runs.

In-memory registry, one per control-api process - same tradeoff as
query_profile.py (see that module's docstring): simple, fine for a single
control-api replica, and resets on restart. A real multi-replica deployment
would want this in the shared database instead; noted here rather than
quietly assumed.

Progress is only ever reported for stages where a real number exists:
  - "querying"  - no byte/row progress available (qpython returns the whole
    result in one IPC message, it doesn't stream), so this stage shows only
    elapsed time in the UI, never a fabricated percentage.
  - "writing"   - likewise, a single pyarrow write call; elapsed time only.
  - "uploading" - real byte-level progress from boto3 / azure-storage-blob's
    callbacks (export_sinks.py), because that's the one phase where the
    underlying SDK actually reports transferred bytes as it goes.
This mirrors the same "real milestones over a fabricated percentage"
philosophy already used for shard sync status (lib/syncProgress.js) and the
existing self-healing recovery ETA (RecoveryWatch.jsx).
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable

from . import export_sinks, parquet_export
from .kdb_client import gateway_client

log = logging.getLogger("export_jobs")

# Row cap for the background path - independent of, and much higher than,
# the interactive query workspace's row_limit_max (query_service.DEFAULT/MAX_ROW_LIMIT),
# since the whole point of this path is pulling more than the UI grid ever holds.
BULK_ROW_LIMIT_MAX = int(os.environ.get("EXPORT_BULK_ROW_LIMIT_MAX", "5000000"))

# Gateway-pressure thresholds - same signal Autoscale.jsx already reads from
# /metrics/snapshot's componentMetrics (tpQueue/tpSubLag), checked here
# server-side before a bulk pull starts. This never triggers a scaling action
# by itself - it flags the job so the UI can point the operator at the
# Autoscaling page's real Apply flow, the same human-in-the-loop design
# already used there (auto-apply defaults off for the same reason: changing
# shard count is a real topology change, not something to fire unattended
# as a side effect of an unrelated export).
PRESSURE_QUEUE_THRESHOLD = int(os.environ.get("EXPORT_GATEWAY_PRESSURE_QUEUE_THRESHOLD", "200000"))
PRESSURE_LAG_THRESHOLD = int(os.environ.get("EXPORT_GATEWAY_PRESSURE_LAG_THRESHOLD", "1000"))

_JOBS: dict[str, "ExportJobState"] = {}
_LOCK = threading.Lock()


@dataclass
class ExportJobState:
    id: str
    actor: str
    query: str
    targets: list[str]
    destination: dict
    status: str = "queued"          # queued | running | succeeded | failed
    stage: str = "queued"           # queued | querying | writing | uploading | done | failed
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    row_count: int | None = None
    bytes_done: int | None = None
    bytes_total: int | None = None
    gateway_pressure: dict | None = None
    result: dict | None = None
    error: str | None = None

    def touch(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.updated_at = time.time()

    def to_api(self) -> dict:
        d = asdict(self)
        d["elapsed_sec"] = round(time.time() - self.created_at, 1)
        return d


def check_gateway_pressure() -> dict:
    """Real signal, not a guess: the same componentMetrics the Autoscaling
    page already polls. Returns {elevated, rows: [...], summary}."""
    try:
        rows = gateway_client.component_metrics()
    except Exception as exc:  # noqa: BLE001 - can't tell, don't block the export on it
        return {"elevated": False, "rows": [], "summary": f"gateway metrics unavailable ({exc}) - proceeding without a pressure check"}

    hot = [r for r in (rows or [])
           if (r.get("tpQueue") or 0) > PRESSURE_QUEUE_THRESHOLD or (r.get("tpSubLag") or 0) > PRESSURE_LAG_THRESHOLD]
    if not hot:
        return {"elevated": False, "rows": [], "summary": "gateway load is normal"}
    labels = ", ".join(str(r.get("shard")) for r in hot)
    return {"elevated": True, "rows": hot,
            "summary": f"{labels} under elevated load (queue/lag above threshold) - "
                       "this export may run slowly and add to that load; consider scaling up first"}


def create_job(actor: str, query: str, targets: list[str], destination: dict) -> ExportJobState:
    job = ExportJobState(id=str(uuid.uuid4()), actor=actor, query=query, targets=targets, destination=destination)
    with _LOCK:
        _JOBS[job.id] = job
    return job


def get_job(job_id: str) -> ExportJobState | None:
    with _LOCK:
        return _JOBS.get(job_id)


def list_jobs(actor: str | None = None, limit: int = 50) -> list[dict]:
    with _LOCK:
        jobs = list(_JOBS.values())
    if actor:
        jobs = [j for j in jobs if j.actor == actor]
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return [j.to_api() for j in jobs[:limit]]


def run_export_job(job_id: str, fetch_grid: Callable[[], tuple[list[str], list[list]]]) -> None:
    """The background worker. `fetch_grid` runs the actual query (kept as an
    injected callable so this function - and its tests - don't need to know
    anything about kdb+/IPC; routers/query.py supplies the real one, using
    the exact same routing/federation logic as the interactive /query/run).
    """
    job = get_job(job_id)
    if job is None:
        return
    job.touch(status="running", stage="querying")

    pressure = check_gateway_pressure()
    job.touch(gateway_pressure=pressure)

    tmp_path = None
    try:
        columns, rows = fetch_grid()
        job.touch(row_count=len(rows))

        job.touch(stage="writing")
        table = parquet_export.build_arrow_table(columns, rows)
        import pyarrow.parquet as pq
        fd, tmp_path = tempfile.mkstemp(suffix=".parquet")
        os.close(fd)
        pq.write_table(table, tmp_path, compression="snappy")
        job.touch(bytes_total=os.path.getsize(tmp_path))

        job.touch(stage="uploading", bytes_done=0)

        def _progress(done, total):
            job.touch(bytes_done=done, bytes_total=total)

        provider = job.destination.get("provider")
        if provider == "s3":
            result = export_sinks.upload_to_s3(
                tmp_path, job.destination.get("bucket"), job.destination.get("key"),
                region=job.destination.get("region"), progress_cb=_progress)
        elif provider == "adls":
            result = export_sinks.upload_to_adls(
                tmp_path, job.destination.get("container"), job.destination.get("path"), progress_cb=_progress)
        else:
            raise export_sinks.SinkNotConfigured(f"unknown destination provider '{provider}' - use 's3' or 'adls'")

        job.touch(status="succeeded", stage="done", result=result)
        log.info("export job %s succeeded: %s", job_id, result)
    except Exception as exc:  # noqa: BLE001 - always record, never leave a job silently stuck
        log.warning("export job %s failed: %s", job_id, exc)
        job.touch(status="failed", stage="failed", error=str(exc))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
