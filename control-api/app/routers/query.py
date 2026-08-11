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
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel

from .. import export_jobs
from .. import llm_runtime_config
from .. import nl2q as nl2q_mod
from .. import parquet_export
from .. import q_codegen
from .. import query_advisor
from .. import query_profile
from .. import query_router
from .. import query_service as qs
from .. import topology
from ..llm_provider import LLMError
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


def _run_one(target_id: str, query: str, limit: int, allow_write: bool,
             max_allowed: int = qs.MAX_ROW_LIMIT) -> dict:
    """Run against a single target; returns {target, ok, grid|error, elapsed_ms,
    connect_ms, query_ms}. The last two split elapsed_ms into "getting a
    connection open" vs "the query itself" - see query_profile.py.
    `max_allowed` defaults to the interactive workspace's ceiling; the
    background bulk-export path (_fetch_grid below) passes a much higher one
    - see query_service.clamp_limit."""
    import time

    # Gateway is a router, not a physical table host; for plain market-table
    # queries we fan out to RDB shards and merge, so users can keep writing
    # standard q like `select ... from trade ...` against target "gateway".
    # Intelligent routing (query_router.py): if the query's `sym` filter
    # resolves to a strict subset of shards, only fan out to those - a shard
    # that doesn't own any referenced symbol would return zero rows anyway
    # (topology.shard_of is the SAME partitioning the gateway itself uses),
    # so this is a pure latency win, never a correctness tradeoff. Falls
    # back to the full shard set whenever it can't confidently narrow.
    if target_id == "gateway" and _MARKET_TABLE_RE.search(query):
        t0 = time.perf_counter()
        all_shard_targets = [t["id"] for t in _targets() if t["id"].startswith("rdb-")]
        routed = query_router.route_shards(query, _SHARD_COUNT)
        if routed:
            shard_targets = [f"rdb-{sid}" for sid in routed if f"rdb-{sid}" in all_shard_targets]
        else:
            shard_targets = all_shard_targets
        skipped_targets = [t for t in all_shard_targets if t not in shard_targets]
        results = [_run_one(t, query, limit, allow_write, max_allowed) for t in shard_targets]
        labeled = [(r["target"], r["grid"]) for r in results if r["ok"]]
        if not labeled:
            return {
                "target": target_id,
                "ok": False,
                "error": "all shard RDB targets failed: " + "; ".join(
                    f"{r['target']}: {r.get('error', 'unknown error')}" for r in results
                ),
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                "routed_shards": shard_targets if routed else None,
            }
        grid = qs.combine_results(labeled, add_provenance=True, limit=limit)
        grid["query"] = query
        grid["kind"] = "gateway-federated"
        grid["routed_shards"] = shard_targets if routed else None
        grid["skipped_shards"] = skipped_targets if routed else []
        if re.search(r"\bby\b", query, re.IGNORECASE):
            grid["warning"] = (
                "grouped query merged from per-shard partials; if this is an aggregate, "
                "re-aggregate by key for exact global totals"
            )
        query_ms = round(sum(r["elapsed_ms"] for r in results), 1)
        return {
            "target": target_id,
            "ok": True,
            "grid": grid,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            "connect_ms": 0.0,
            "query_ms": query_ms,
            "routed_shards": shard_targets if routed else None,
        }

    host, port = _resolve(target_id)
    t0 = time.perf_counter()
    try:
        conn = _connect(host, port)
    except Exception as exc:  # noqa: BLE001
        return {"target": target_id, "ok": False, "error": f"unreachable {host}:{port}: {exc}",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}
    t1 = time.perf_counter()
    try:
        grid = qs.run_query(query, conn, limit=limit, allow_write=allow_write, max_allowed=max_allowed)
        t2 = time.perf_counter()
        return {"target": target_id, "ok": True, "grid": grid,
                "elapsed_ms": round((t2 - t0) * 1000, 1),
                "connect_ms": round((t1 - t0) * 1000, 1),
                "query_ms": round((t2 - t1) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"target": target_id, "ok": False, "error": f"query error: {exc}",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                "connect_ms": round((t1 - t0) * 1000, 1)}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _fetch_grid(targets: list[str], query: str, limit: int) -> tuple[list[str], list[list]]:
    """Run a (read-only) query across one or more targets and return a plain
    (columns, rows) grid - the same per-target routing/federation `_run_one`
    already implements for the interactive /run endpoint, reused here for the
    background export job so both paths can never disagree about what a
    given target set + query actually returns. Always read-only (no
    allow_write path for a background bulk pull), and always uses the bulk
    row ceiling (export_jobs.BULK_ROW_LIMIT_MAX), not the interactive one -
    this is the entire reason the background path exists."""
    for t in targets:
        _resolve(t)
    max_allowed = export_jobs.BULK_ROW_LIMIT_MAX
    limit = min(limit, max_allowed)
    results = [_run_one(t, query, limit, False, max_allowed) for t in targets]
    labeled = [(r["target"], r["grid"]) for r in results if r["ok"]]
    if not labeled:
        raise HTTPException(status_code=502, detail="all targets failed: " +
                            "; ".join(f"{r['target']}: {r.get('error')}" for r in results))
    combined = labeled[0][1] if len(labeled) == 1 else qs.combine_results(labeled, add_provenance=True, limit=limit)
    return combined["columns"], combined["rows"]


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
        query_profile.record(
            actor=user.email, target=targets[0], query=body.query,
            ok=res["ok"], row_count=res["grid"]["row_count"] if res["ok"] else None,
            elapsed_ms=res["elapsed_ms"], connect_ms=res.get("connect_ms"), query_ms=res.get("query_ms"),
            routed_shards=res.get("routed_shards"), error=None if res["ok"] else res["error"],
        )
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
    total_rows = sum(r["grid"]["row_count"] for r in results if r["ok"])
    query_profile.record(
        actor=user.email, target=",".join(targets), query=body.query,
        ok=bool(labeled), row_count=total_rows if labeled else None,
        elapsed_ms=round(sum(r["elapsed_ms"] for r in results), 1),
        query_ms=round(sum(r.get("query_ms") or 0 for r in results), 1),
        routed_shards=None,
        error=None if labeled else "; ".join(f"{r['target']}: {r.get('error')}" for r in results),
    )
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


class ParquetExportBody(BaseModel):
    columns: list[str]
    rows: list[list]
    filename: str | None = None


@router.post("/export/parquet")
def export_parquet(body: ParquetExportBody, user: CurrentUser = Depends(require_tenant_scope)):
    """Download the CURRENT result grid - whatever's already on screen in the
    query workspace - as a real Parquet file. Deliberately takes the grid the
    UI already fetched rather than re-running the query, so what you download
    always matches what you're looking at (including any client-side
    federation/merge across targets), not a fresh, possibly-different read.
    See parquet_export.py for the type-inference rules and the 10GB local cap
    (shared with the background S3/ADLS export path) - past that, use
    /query/export/background instead, which streams to a temp file and
    uploads rather than holding the whole thing in one response body.
    """
    if not body.columns:
        raise HTTPException(status_code=400, detail="no columns to export")
    if not body.rows:
        raise HTTPException(status_code=400, detail="no rows to export")

    try:
        data = parquet_export.write_parquet_bytes(body.columns, body.rows)
    except parquet_export.ExportTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = body.filename or f"query-result-{stamp}.parquet"
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class BackgroundExportDestination(BaseModel):
    provider: str          # "s3" | "adls"
    bucket: str | None = None    # s3
    key: str | None = None       # s3
    region: str | None = None    # s3, optional
    container: str | None = None  # adls
    path: str | None = None       # adls


class BackgroundExportBody(BaseModel):
    target: str = "gateway"
    targets: list[str] | None = None
    query: str
    limit: int = export_jobs.BULK_ROW_LIMIT_MAX
    destination: BackgroundExportDestination


@router.post("/export/background")
def start_background_export(body: BackgroundExportBody, background_tasks: BackgroundTasks,
                             user: CurrentUser = Depends(require_tenant_scope)):
    """Kick off a bulk export to S3/ADLS as a background job and return
    immediately with a job id to poll - see export_jobs.py for why this
    exists (the interactive /run path is capped at query_service.MAX_ROW_LIMIT
    and runs synchronously in the request; this path is for pulls larger than
    that, without holding the HTTP connection open while it runs, and without
    ever holding the whole result in one response body the way the local
    Parquet download does). Read-only, same as every other query path here -
    there is no write variant of a background export.
    """
    ok, reason = qs.check_readonly(body.query)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    targets = body.targets or [body.target]
    for t in targets:
        _resolve(t)
    if body.destination.provider not in ("s3", "adls"):
        raise HTTPException(status_code=400, detail="destination.provider must be 's3' or 'adls'")
    if body.destination.provider == "s3" and not body.destination.key:
        raise HTTPException(status_code=400, detail="s3 destination needs a key")
    if body.destination.provider == "adls" and not body.destination.path:
        raise HTTPException(status_code=400, detail="adls destination needs a path")

    job = export_jobs.create_job(actor=user.email, query=body.query, targets=targets,
                                 destination=body.destination.model_dump())
    background_tasks.add_task(export_jobs.run_export_job, job.id,
                              lambda: _fetch_grid(targets, body.query, body.limit))
    return job.to_api()


@router.get("/export/jobs/{job_id}")
def get_background_export(job_id: str, user: CurrentUser = Depends(require_tenant_scope)):
    job = export_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="export job not found")
    return job.to_api()


@router.get("/export/jobs")
def list_background_exports(limit: int = 20, user: CurrentUser = Depends(require_tenant_scope)):
    return {"jobs": export_jobs.list_jobs(actor=user.email, limit=limit)}


class Nl2qBody(BaseModel):
    text: str
    target: str = "gateway"


@router.post("/nl2q")
def nl2q(body: Nl2qBody, user: CurrentUser = Depends(require_tenant_scope)):
    """Natural-language -> q, via whatever LLM provider is configured
    (app/llm_provider.py). Always 200 - the caller (the query workspace UI)
    treats `ok: false` as "fall back to the offline regex generator", not
    as a hard error; there's no reason to break the UI just because no
    model is configured or a provider call timed out.
    """
    text = (body.text or "").strip()
    if not text:
        return {"ok": False, "q": None, "provider": None, "error": "empty request"}

    host = port = None
    try:
        host, port = _resolve(body.target)
    except HTTPException:
        pass  # schema-only fallback still works fine without a live target

    try:
        q = nl2q_mod.generate(text, host=host, port=port)
        return {"ok": True, "q": q, "provider": llm_runtime_config.get().provider, "error": None}
    except nl2q_mod.NotConfigured:
        return {"ok": False, "q": None, "provider": None, "error": "no LLM provider configured"}
    except LLMError as exc:
        return {"ok": False, "q": None, "provider": llm_runtime_config.get().provider, "error": str(exc)}


class CodegenBody(BaseModel):
    text: str
    target: str = "gateway"


@router.post("/codegen")
def codegen(body: CodegenBody, user: CurrentUser = Depends(require_tenant_scope)):
    """Plain language -> a q FUNCTION (multi-line, not a single query - see
    app/q_codegen.py). Same "generate into the editor, human reviews and
    clicks Run" pattern as /nl2q. There is no offline fallback here - unlike
    a SELECT, a general function has no safe regex-based generator to fall
    back to, so `ok: false` just means try again or write it by hand.
    """
    text = (body.text or "").strip()
    if not text:
        return {"ok": False, "code": None, "provider": None, "error": "empty request"}

    host = port = None
    try:
        host, port = _resolve(body.target)
    except HTTPException:
        pass

    try:
        code = q_codegen.generate(text, host=host, port=port)
        return {"ok": True, "code": code, "provider": llm_runtime_config.get().provider, "error": None}
    except q_codegen.NotConfigured:
        return {"ok": False, "code": None, "provider": None, "error": "no LLM provider configured"}
    except LLMError as exc:
        return {"ok": False, "code": None, "provider": llm_runtime_config.get().provider, "error": str(exc)}


class AnalyzeBody(BaseModel):
    q: str
    target: str = "gateway"


@router.post("/analyze")
def analyze(body: AnalyzeBody, user: CurrentUser = Depends(require_tenant_scope)):
    """Explain / flag issues / suggest an optimized rewrite / suggest
    related follow-up queries for a q expression already in the editor.
    The deterministic shard-routing tip (query_router.py, via
    query_advisor.py) always runs even with no LLM configured - only the
    explanation/optimize/suggest fields depend on a model.
    """
    q = (body.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="empty query")

    host = port = None
    try:
        host, port = _resolve(body.target)
    except HTTPException:
        pass

    return query_advisor.analyze(q, _SHARD_COUNT, host=host, port=port)


@router.get("/history")
def history(limit: int = 50, user: CurrentUser = Depends(require_tenant_scope)):
    """Recent query executions with profiling - see query_profile.py. In-memory,
    per control-api process; resets on restart (see module docstring for why)."""
    return {"entries": query_profile.recent(limit), "stats": query_profile.stats()}


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
