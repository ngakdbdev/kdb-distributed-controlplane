"""
query.py (router) - the live query workspace backend. Connects to a q process
via IPC, runs a (read-only by default) query, and returns a grid for the UI.

Reachability note: this connects DIRECTLY from the control plane to the cluster,
so it works when the control plane can reach the q processes (on-prem,
single-tenant, or a demo). For the multi-tenant SaaS where the control plane has
no network path into a tenant's environment, the query should instead be
relayed through that tenant's agent - a natural follow-on (the agent already
lives in-cluster). Targets/host resolution here come from env + topology.
"""
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import query_service as qs
from .. import topology
from .auth import CurrentUser, require_tenant_scope

router = APIRouter(prefix="/query", tags=["query"])

# where the control plane dials q. Short service names resolve under compose/k8s
# DNS; override for other layouts.
_GW_HOST = os.environ.get("QUERY_GATEWAY_HOST", "gateway")
_GW_PORT = int(os.environ.get("QUERY_GATEWAY_PORT", "5050"))
_SHARD_COUNT = int(os.environ.get("QUERY_SHARD_COUNT", os.environ.get("SHARD_COUNT", "2")))
_ALLOW_WRITE = os.environ.get("QUERY_ALLOW_WRITE", "").lower() in ("1", "true", "yes")


def _targets() -> list[dict]:
    out = [{"id": "gateway", "label": "Gateway (all shards)", "host": _GW_HOST, "port": _GW_PORT}]
    for s in topology.shards(_SHARD_COUNT):
        rdb = topology.gateway_host(s, "rdb")
        host, port = rdb.rsplit(":", 1)
        out.append({"id": f"rdb-{s.id}", "label": f"RDB {s.label} (today)", "host": host, "port": int(port)})
    return out


def _resolve(target_id: str):
    for t in _targets():
        if t["id"] == target_id:
            return t["host"], t["port"]
    raise HTTPException(status_code=404, detail=f"unknown target '{target_id}'")


def _connect(host: str, port: int):
    """Open a short-lived qpython IPC connection. Real network - not exercised
    in CI (tests inject a fake conn into query_service.run_query directly)."""
    from qpython import qconnection
    conn = qconnection.QConnection(host=host, port=port, pandas=False,
                                   timeout=int(os.environ.get("QUERY_TIMEOUT_SEC", "15")))
    conn.open()
    return conn


@router.get("/targets")
def targets(user: CurrentUser = Depends(require_tenant_scope)):
    return {"targets": _targets(), "allow_write": _ALLOW_WRITE,
            "row_limit_default": qs.DEFAULT_ROW_LIMIT, "row_limit_max": qs.MAX_ROW_LIMIT}


class RunBody(BaseModel):
    target: str = "gateway"
    query: str
    limit: int = qs.DEFAULT_ROW_LIMIT
    allow_write: bool = False


@router.post("/run")
def run(body: RunBody, user: CurrentUser = Depends(require_tenant_scope)):
    allow_write = body.allow_write and _ALLOW_WRITE
    if body.allow_write and not _ALLOW_WRITE:
        raise HTTPException(status_code=403,
                            detail="writes are disabled on this deployment (QUERY_ALLOW_WRITE)")
    # validate read-only BEFORE opening a connection
    if not allow_write:
        ok, reason = qs.check_readonly(body.query)
        if not ok:
            raise HTTPException(status_code=400, detail=reason)

    host, port = _resolve(body.target)
    try:
        conn = _connect(host, port)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"could not reach {host}:{port}: {exc}")
    import time
    t0 = time.perf_counter()
    try:
        payload = qs.run_query(body.query, conn, limit=body.limit, allow_write=allow_write)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - surface a q error to the user
        raise HTTPException(status_code=400, detail=f"query error: {exc}")
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    payload["target"] = body.target
    payload["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return payload


@router.get("/tables")
def tables(target: str = "gateway", user: CurrentUser = Depends(require_tenant_scope)):
    """Convenience: list the tables on a target (runs `tables[]`)."""
    host, port = _resolve(target)
    try:
        conn = _connect(host, port)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"could not reach {host}:{port}: {exc}")
    try:
        result = conn("tables[]")
        payload = qs.shape_result(result, limit=200)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"query error: {exc}")
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return {"tables": [r[0] for r in payload.get("rows", [])]}
