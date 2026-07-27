import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..orchestrator import orchestrator
from ..kdb_client import gateway_client

router = APIRouter(prefix="/metrics", tags=["metrics"])
log = logging.getLogger("metrics")


def _snapshot() -> dict:
    return {
        "health": gateway_client.health(),
        "transitLag": gateway_client.transit_lag(),
        "rowCounts": gateway_client.row_counts(),
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
