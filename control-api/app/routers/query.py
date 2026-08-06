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
import re

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
_MARKET_TABLE_RE = re.compile(r"\bfrom\s+(trade|risk)\b", re.IGNORECASE)


def _targets() -> list[dict]:
    out = [{"id": "gateway", "label": "Gateway (all shards)", "host": _GW_HOST, "port": _GW_PORT}]
    for s in topology.shards(_SHARD_COUNT):
        rdb = topology.gateway_host(s, "rdb")
        host, port = rdb.rsplit(":", 1)
        out.append({"id": f"rdb-{s.id}", "label": f"RDB {s.label} (today)", "host": host, "port": int(port)})
    for s in topology.shards(_SHARD_COUNT):
        tp = topology.gateway_host(s, "tickerplant")
        host, port = tp.rsplit(":", 1)
        out.append({"id": f"tp-{s.id}", "label": f"Tickerplant {s.label} (live buffer)", "host": host, "port": int(port)})
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
    targets: list[str] | None = None   # federate across several (scatter-gather)
    query: str
    limit: int = qs.DEFAULT_ROW_LIMIT
    allow_write: bool = False


def _run_one(target_id: str, query: str, limit: int, allow_write: bool) -> dict:
    """Run against a single target; returns {target, ok, grid|error, elapsed_ms}."""
    import time

    # Gateway is a router, not a physical table host; for plain market-table
    # queries we fan out to all RDB shards and merge, so users can keep writing
    # standard q like `select ... from trade ...` against target "gateway".
    if target_id == "gateway" and _MARKET_TABLE_RE.search(query):
        t0 = time.perf_counter()
        shard_targets = [t["id"] for t in _targets() if t["id"].startswith("rdb-")]
        results = [_run_one(t, query, limit, allow_write) for t in shard_targets]
        labeled = [(r["target"], r["grid"]) for r in results if r["ok"]]
        if not labeled:
            return {
                "target": target_id,
                "ok": False,
                "error": "all shard RDB targets failed: " + "; ".join(
                    f"{r['target']}: {r.get('error', 'unknown error')}" for r in results
                ),
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        grid = qs.combine_results(labeled, add_provenance=True, limit=limit)
        grid["kind"] = "gateway-federated"
        if re.search(r"\bby\b", query, re.IGNORECASE):
            grid["warning"] = (
                "grouped query merged from per-shard partials; if this is an aggregate, "
                "re-aggregate by key for exact global totals"
            )
        return {
            "target": target_id,
            "ok": True,
            "grid": grid,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    host, port = _resolve(target_id)
    t0 = time.perf_counter()
    try:
        conn = _connect(host, port)
    except Exception as exc:  # noqa: BLE001
        return {"target": target_id, "ok": False, "error": f"unreachable {host}:{port}: {exc}",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}
    try:
        grid = qs.run_query(query, conn, limit=limit, allow_write=allow_write)
        return {"target": target_id, "ok": True, "grid": grid,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"target": target_id, "ok": False, "error": f"query error: {exc}",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


@router.post("/run")
def run(body: RunBody, user: CurrentUser = Depends(require_tenant_scope)):
    allow_write = body.allow_write and _ALLOW_WRITE
    if body.allow_write and not _ALLOW_WRITE:
        raise HTTPException(status_code=403,
                            detail="writes are disabled on this deployment (QUERY_ALLOW_WRITE)")
    # validate read-only BEFORE opening any connection
    if not allow_write:
        ok, reason = qs.check_readonly(body.query)
        if not ok:
            raise HTTPException(status_code=400, detail=reason)

    targets = body.targets or [body.target]
    # validate every target resolves before running
    for t in targets:
        _resolve(t)

    # single target: return the grid directly (unchanged shape)
    if len(targets) == 1:
        res = _run_one(targets[0], body.query, body.limit, allow_write)
        if not res["ok"]:
            raise HTTPException(status_code=502 if "unreachable" in res["error"] else 400,
                                detail=res["error"])
        payload = res["grid"]
        payload["target"] = targets[0]
        payload["elapsed_ms"] = res["elapsed_ms"]
        return payload

    # federated: fan out, combine successful grids, report per-target status
    results = [_run_one(t, body.query, body.limit, allow_write) for t in targets]
    labeled = [(r["target"], r["grid"]) for r in results if r["ok"]]
    if not labeled:
        raise HTTPException(status_code=502, detail="all targets failed: " +
                            "; ".join(f"{r['target']}: {r['error']}" for r in results))
    combined = qs.combine_results(labeled, add_provenance=True, limit=body.limit)
    combined["query"] = body.query
    combined["per_target"] = [{"target": r["target"], "ok": r["ok"],
                               "rows": r["grid"]["row_count"] if r["ok"] else 0,
                               "error": r.get("error"), "elapsed_ms": r["elapsed_ms"]}
                              for r in results]
    return combined


@router.get("/tables")
def tables(target: str = "gateway", user: CurrentUser = Depends(require_tenant_scope)):
    """Convenience: list the tables on a target (runs `tables[]`)."""
    if target == "gateway":
        # Gateway routes `trade`/`risk` across shards; advertise those directly
        # so the query UI remains intuitive.
        return {"tables": ["trade", "risk"]}

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
