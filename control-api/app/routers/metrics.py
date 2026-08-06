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
    """Pushes a fresh snapshot every second - the dashboard's live feed."""
    await ws.accept()
    try:
        while True:
            await ws.send_json(_snapshot())
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        log.info("metrics stream client disconnected")
