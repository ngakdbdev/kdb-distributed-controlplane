"""
prometheus_metrics.py - real Prometheus exposition-format metrics for
Vantik, rendered from the SAME live data sources the JSON dashboard
endpoints already use (routers/metrics.py's _snapshot() - real kdb+ IPC
counters/gauges via the gateway - plus the Order/AuditEvent tables) - no
separate collection pipeline, no synthetic numbers. Scraped via GET
/metrics (see main.py) - Prometheus's own convention for where an exporter
lives, distinct from the existing JSON GET /metrics/snapshot this reuses.

Deliberately unauthenticated, same as /metrics/snapshot itself (see that
endpoint's own docstring: "no tenant filtering exists on this data today
either"). This control plane has no tenant-scoped metrics-auth story yet,
and a Prometheus scrape config doesn't participate in this app's JWT flow
anyway. Don't expose this route through an ingress reachable from outside
your own scrape network without addressing that first - restrict it at
the network/ingress layer (see helm/kdb-control-plane/templates/
servicemonitor.yaml's own comment on scoping this to Prometheus's
in-cluster ServiceMonitor path, not the public ingress host).

Every value here is a Gauge, deliberately, even the ones that only ever
increase (tpRecv, order counts, ...): they're all point-in-time reads of a
value some OTHER system already tracks (kdb+'s own counters, this table's
row count) - this process never increments anything in memory, so there's
nothing for a prometheus_client Counter (which only supports .inc()) to
correctly represent. Prometheus's own rate()/increase() functions work
identically on a monotonic Gauge as on a Counter; the TYPE annotation
difference is informational; using Counter here would require faking a
running total, which is worse than an honest Gauge.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Gauge, generate_latest, CONTENT_TYPE_LATEST
from sqlmodel import Session, func, select

from .models import AuditEvent, Order
from .routers.metrics import _snapshot


def render_metrics(session: Session) -> tuple[bytes, str]:
    """Returns (body, content_type) - main.py's /metrics route just passes
    both straight through as the Response."""
    registry = CollectorRegistry()
    snap = _snapshot()

    # ---- process/service liveness (orchestrator.status_all(), same data
    # Topology.jsx renders) ----
    service_up = Gauge(
        "vantik_service_up", "1 if the named data-plane/control-plane process is running, 0 otherwise.",
        ["service"], registry=registry,
    )
    services = snap.get("services") or {}
    if isinstance(services, dict):
        for name, status in services.items():
            service_up.labels(service=str(name)).set(1.0 if str(status) == "running" else 0.0)

    # ---- per-shard component metrics (real gateway counters/gauges -
    # same .gw.componentMetrics[] data Overview/Metrics/Autoscale derive
    # msg/s and latency from client-side) ----
    labels = ["shard"]
    tp_recv = Gauge("vantik_tp_recv_total", "Cumulative messages received by this shard's tickerplant.", labels, registry=registry)
    tp_pub = Gauge("vantik_tp_pub_total", "Cumulative messages published by this shard's tickerplant.", labels, registry=registry)
    tp_queue_bytes = Gauge("vantik_tp_queue_bytes", "Current tickerplant publish queue depth, bytes.", labels, registry=registry)
    tp_sub_lag_bytes = Gauge("vantik_tp_sub_lag_bytes", "Current slowest-subscriber lag, bytes (real drop threshold is 50MB - see tick.q's SLOW_SUB_MAX_BYTES).", labels, registry=registry)
    tp_publish_latency_us = Gauge("vantik_tp_publish_latency_us", "Tickerplant publish latency, microseconds.", labels, registry=registry)
    tp_log_latency_us = Gauge("vantik_tp_log_latency_us", "Tickerplant log-write latency, microseconds.", labels, registry=registry)
    rdb_rows_trade = Gauge("vantik_rdb_rows_trade", "Rows currently held in this shard's rdb trade table.", labels, registry=registry)
    rdb_rows_risk = Gauge("vantik_rdb_rows_risk", "Rows currently held in this shard's rdb risk table.", labels, registry=registry)
    rdb_reconnects = Gauge("vantik_rdb_reconnects_total", "Cumulative rdb reconnect count for this shard.", labels, registry=registry)
    rdb_connected = Gauge("vantik_rdb_connected", "1 if this shard's rdb is connected to its tickerplant, 0 otherwise.", labels, registry=registry)
    wdb_connected = Gauge("vantik_wdb_connected", "1 if this shard's wdb is connected to its tickerplant, 0 otherwise.", labels, registry=registry)
    wdb_reconnects = Gauge("vantik_wdb_reconnects_total", "Cumulative wdb reconnect count for this shard.", labels, registry=registry)

    for row in (snap.get("componentMetrics") or []):
        if not isinstance(row, dict):
            continue
        shard = str(row.get("shard") or "unknown")
        _set_if_present(tp_recv, shard, row.get("tpRecv"))
        _set_if_present(tp_pub, shard, row.get("tpPub"))
        _set_if_present(tp_queue_bytes, shard, row.get("tpQueue"))
        _set_if_present(tp_sub_lag_bytes, shard, row.get("tpSubLag"))
        _set_if_present(tp_publish_latency_us, shard, row.get("tpPubLatencyUs"))
        _set_if_present(tp_log_latency_us, shard, row.get("tpLogLatencyUs"))
        _set_if_present(rdb_rows_trade, shard, row.get("rdbRowsTrade"))
        _set_if_present(rdb_rows_risk, shard, row.get("rdbRowsRisk"))
        _set_if_present(rdb_reconnects, shard, row.get("rdbReconnects"))
        if row.get("rdbConnected") is not None:
            rdb_connected.labels(shard=shard).set(1.0 if row.get("rdbConnected") else 0.0)
        if row.get("wdbConnected") is not None:
            wdb_connected.labels(shard=shard).set(1.0 if row.get("wdbConnected") else 0.0)
        _set_if_present(wdb_reconnects, shard, row.get("wdbReconnects"))

    # ---- cluster-wide row counts (same numbers Overview's KPI row shows) ----
    row_count = Gauge("vantik_row_count", "Rows currently held across all shards' rdb, by table.", ["table"], registry=registry)
    for table, value in (snap.get("rowCounts") or {}).items():
        _set_if_present(row_count, table, value, label_kw="table")

    # ---- orders (real DB - the same table Orders.jsx's blotter reads) ----
    orders_total = Gauge("vantik_orders_total", "Orders in this deployment's database, by status, all tenants.", ["status"], registry=registry)
    for status, count in session.exec(select(Order.status, func.count()).group_by(Order.status)).all():
        orders_total.labels(status=status or "unknown").set(count)

    # ---- audit / self-healing (real DB - same table AuditLog.jsx reads) ----
    audit_total = Gauge("vantik_audit_events_total", "Audit events recorded, by actor and outcome, all tenants, all time.", ["actor", "outcome"], registry=registry)
    for actor, outcome, count in session.exec(
        select(AuditEvent.actor, AuditEvent.outcome, func.count()).group_by(AuditEvent.actor, AuditEvent.outcome)
    ).all():
        audit_total.labels(actor=actor or "unknown", outcome=outcome or "unknown").set(count)

    return generate_latest(registry), CONTENT_TYPE_LATEST


def _set_if_present(gauge: Gauge, label_value: str, value, label_kw: str = "shard") -> None:
    if value is None:
        return
    try:
        gauge.labels(**{label_kw: label_value}).set(float(value))
    except (TypeError, ValueError):
        pass
