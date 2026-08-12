import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..orchestrator import orchestrator
from ..kdb_client import gateway_client

router = APIRouter(prefix="/metrics", tags=["metrics"])
log = logging.getLogger("metrics")


def _safe_metric(name: str, fn, default):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        log.warning("metrics snapshot field failed (%s): %s", name, exc)
        return default


def _snapshot() -> dict:
    return {
        "health": _safe_metric("health", gateway_client.health, []),
        "transitLag": _safe_metric("transitLag", gateway_client.transit_lag, []),
        "componentMetrics": _safe_metric("componentMetrics", gateway_client.component_metrics, []),
        "rowCounts": _safe_metric("rowCounts", gateway_client.row_counts, {"trade": 0, "risk": 0}),
        "services": orchestrator.status_all(),
    }


@router.get("/snapshot")
def snapshot():
    """One-shot metrics pull, used by the dashboard on initial page load."""
    return _snapshot()


@router.websocket("/stream")
async def stream(ws: WebSocket):
    """Pushes a fresh snapshot every second - the dashboard's live feed.

    _snapshot() makes synchronous kdb+ IPC calls (kdb_client.GatewayClient
    is plain blocking qpython, up to gateway_timeout_sec per call, several
    calls per snapshot). Calling it directly here would block THIS
    process's single asyncio event loop for however long the gateway takes
    to answer - freezing every other concurrent request (every page's HTTP
    calls, every other client's metrics stream) for as long as one slow or
    hanging gateway call takes, not just this one dashboard. asyncio.to_thread
    runs it on a worker thread instead, so a slow/unresponsive gateway
    degrades to a stale metrics tick, not a frozen server. (The plain GET
    /metrics/snapshot above doesn't need this - FastAPI already runs a
    sync `def` route handler in a thread pool automatically; only an
    `async def` websocket handler has to opt in explicitly.)
    """
    await ws.accept()
    try:
        while True:
            snapshot = await asyncio.to_thread(_snapshot)
            await ws.send_json(snapshot)
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        log.info("metrics stream client disconnected")
