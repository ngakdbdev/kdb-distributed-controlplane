"""
query_profile.py - a small, in-memory record of recent query executions, for
the query workspace's "Query Analysis" page.

Deliberately NOT the AuditEvent table (models.py/db.log_event): that table is
a compliance/security trail of admin actions, written rarely; ad-hoc query
executions are frequent and their profiling data matters for minutes/hours,
not for an audit retention policy. A bounded in-memory ring buffer is the
right weight for this - it resets on restart, which is fine for "what's been
slow recently", and avoids a DB migration + write-amplification on every
query a user runs.

Not thread/process-safe beyond the GIL's own atomicity guarantees on
list.append - fine for a single-process dev/demo deployment. A multi-replica
control-api would need this centralized (e.g. in the same DB the tenant
already has) rather than per-process; noting it here rather than solving it,
since this deployment runs one control-api replica (sqlite backing store
can't safely support more than one anyway - see config.py).
"""
from __future__ import annotations

import itertools
from collections import deque
from datetime import datetime, timezone
from typing import Any

_MAX_ENTRIES = 200
_history: deque[dict[str, Any]] = deque(maxlen=_MAX_ENTRIES)
_id_seq = itertools.count(1)


def record(**fields: Any) -> dict[str, Any]:
    entry = {
        "id": next(_id_seq),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    _history.append(entry)
    return entry


def recent(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), _MAX_ENTRIES))
    return list(_history)[-limit:][::-1]  # newest first


def stats() -> dict[str, Any]:
    """Aggregate view for the Query Analysis page - how effective has
    intelligent routing actually been, and where is time going."""
    entries = list(_history)
    if not entries:
        return {"count": 0}
    ok = [e for e in entries if e.get("ok")]
    routed = [e for e in ok if e.get("routed_shards") is not None]
    query_ms = [e["query_ms"] for e in ok if isinstance(e.get("query_ms"), (int, float))]
    return {
        "count": len(entries),
        "ok_count": len(ok),
        "error_count": len(entries) - len(ok),
        "routed_count": len(routed),
        "avg_query_ms": round(sum(query_ms) / len(query_ms), 1) if query_ms else None,
        "max_query_ms": max(query_ms) if query_ms else None,
    }
