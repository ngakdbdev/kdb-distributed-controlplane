"""Per-tickerplant live monitoring: dials each TP over IPC and reads its
`.u.stats[]` (counters/gauges) and `.u.health[]` (boolean checks). The control
plane derives per-second rates from deltas between polls on the client side.

The IPC path is real network and not exercised in CI (same policy as the query
router); unreachable/erroring TPs are reported per-entry rather than failing the
whole call, so the dashboard degrades gracefully.
"""
import datetime as _dt
import os

from fastapi import APIRouter, Depends

from .. import topology
from .auth import CurrentUser, require_tenant_scope
from .query import _connect

router = APIRouter(prefix="/tickerplants", tags=["tickerplants"])

_SHARD_COUNT = int(os.environ.get("SHARD_COUNT", "2"))


def _tp_targets():
    out = []
    for s in topology.shards(_SHARD_COUNT):
        hp = topology.gateway_host(s, "tickerplant")
        host, port = hp.rsplit(":", 1)
        out.append((f"tp-{s.id}", s.label, host, int(port)))
    return out


def _scalar(v):
    try:
        import numpy as np
        if isinstance(v, np.generic):
            v = v.item()
    except Exception:  # noqa: BLE001
        pass
    if isinstance(v, bytes):
        return v.decode(errors="replace")
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, bool):
        return v
    return v


def _norm(v):
    """Normalise a qpython value (QDictionary / QList / numpy / bytes) into a
    JSON-safe Python structure."""
    # QDictionary exposes .keys and .values arrays
    keys = getattr(v, "keys", None)
    vals = getattr(v, "values", None)
    if keys is not None and vals is not None and not isinstance(v, (bytes, str, dict)):
        try:
            return {str(_scalar(k)): _norm(val) for k, val in zip(list(keys), list(vals))}
        except Exception:  # noqa: BLE001
            pass
    if isinstance(v, dict):
        return {str(_scalar(k)): _norm(val) for k, val in v.items()}
    try:
        import numpy as np
        if isinstance(v, np.ndarray):
            return [_norm(x) for x in v.tolist()]
    except Exception:  # noqa: BLE001
        pass
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    return _scalar(v)


@router.get("")
def tickerplants(user: CurrentUser = Depends(require_tenant_scope)):
    results = []
    for tid, label, host, port in _tp_targets():
        entry = {"id": tid, "label": f"TP {label}", "shard": tid, "host": host, "port": port}
        try:
            conn = _connect(host, port)
        except Exception as exc:  # noqa: BLE001
            entry.update(ok=False, error=f"unreachable {host}:{port}: {exc}")
            results.append(entry)
            continue
        try:
            entry["stats"] = _norm(conn(".u.stats[]"))
            entry["health"] = _norm(conn(".u.health[]"))
            entry["ok"] = True
        except Exception as exc:  # noqa: BLE001
            entry.update(ok=False, error=f"stats error: {exc}")
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        results.append(entry)
    return {"tickerplants": results}
