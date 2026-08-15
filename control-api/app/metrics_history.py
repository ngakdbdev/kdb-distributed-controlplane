"""
metrics_history.py - periodic background capture of the same numbers
routers/metrics.py's /snapshot already exposes live, persisted so the
Metrics page can show a trend instead of only ever "right now" (previously:
refresh the page, lose the last hour - nothing was retained anywhere).

Shaped after bot_scheduler.py/symbol_discovery.py's own background-thread
pattern: a daemon thread started at app startup, one capture per interval,
a failed capture must never kill the loop.
"""
import logging
import os
import threading
from datetime import datetime, timedelta

from sqlmodel import Session, delete

from .db import engine
from .kdb_client import gateway_client
from .models import MetricsSnapshot
from .orchestrator import orchestrator

log = logging.getLogger("metrics_history")

POLL_SEC = float(os.environ.get("METRICS_SNAPSHOT_INTERVAL_SEC", "60"))
RETENTION_DAYS = int(os.environ.get("METRICS_SNAPSHOT_RETENTION_DAYS", "7"))
ENABLED = os.environ.get("METRICS_HISTORY_ENABLED", "true").lower() == "true"

_stop = threading.Event()
_thread = None


def _safe(fn, default):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        log.warning("metrics history capture field failed: %s", exc)
        return default


def capture_once() -> MetricsSnapshot:
    """One capture, public (not just called from the loop) so tests can
    drive it deterministically - same shape as bot_scheduler.run_once()."""
    services = _safe(orchestrator.status_all, {})
    running = sum(1 for s in services.values() if str(s).lower() == "running")
    rows = _safe(gateway_client.row_counts, {"trade": 0, "risk": 0})
    health = _safe(gateway_client.health, [])
    shards_healthy = sum(1 for h in health
                         if str((h.get("rdb") or {}).get("status", "")).lower() == "up")
    snap = MetricsSnapshot(
        containers_running=running, containers_total=len(services),
        rows_trade=rows.get("trade", 0), rows_risk=rows.get("risk", 0),
        shards_healthy=shards_healthy, shards_total=len(health),
    )
    with Session(engine) as session:
        session.add(snap)
        # Retention purge inline with capture - no separate scheduler
        # needed for a rolling trend window that was never meant to be
        # kept forever (unlike AuditEvent, which is a real audit trail).
        cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
        session.exec(delete(MetricsSnapshot).where(MetricsSnapshot.timestamp < cutoff))
        session.commit()
        session.refresh(snap)
    return snap


def _loop() -> None:
    log.info("metrics history capture up, every %gs, %dd retention", POLL_SEC, RETENTION_DAYS)
    while not _stop.is_set():
        try:
            capture_once()
        except Exception:  # noqa: BLE001
            log.exception("unexpected error in metrics history loop")
        _stop.wait(POLL_SEC)


def start() -> None:
    global _thread
    if not ENABLED:
        log.info("metrics history capture disabled (METRICS_HISTORY_ENABLED=false)")
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="metrics-history", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=5)
