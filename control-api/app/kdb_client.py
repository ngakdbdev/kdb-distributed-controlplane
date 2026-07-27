"""
kdb_client.py - talks to gateway.q over kdb+ IPC using qpython3, so the
control API can pull live health and transit-lag numbers for the dashboard
without needing pykx or a local q install.
"""
import logging

from qpython import qconnection
from qpython.qtype import QException

from .config import settings

log = logging.getLogger("kdb_client")


class GatewayClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.q = None

    def _ensure_connected(self):
        if self.q is not None:
            return
        try:
            self.q = qconnection.QConnection(host=self.host, port=self.port, pandas=False)
            self.q.open()
        except Exception as exc:  # noqa: BLE001
            log.warning("gateway not reachable: %s", exc)
            self.q = None

    def _call(self, expr, default):
        self._ensure_connected()
        if self.q is None:
            return default
        try:
            return self.q(expr)
        except (QException, ConnectionError, OSError) as exc:
            log.warning("gateway call failed (%s): %s", expr, exc)
            self.q = None
            return default

    def health(self) -> list:
        return self._call(".gw.health[]", [])

    def transit_lag(self) -> list:
        return self._call(".gw.transitLag[]", [])

    def row_counts(self) -> dict:
        health = self.health()
        counts = {"trade": 0, "risk": 0}
        for row in health:
            for tier in ("rdb", "idb"):
                stat = row.get(tier, {}) if isinstance(row, dict) else {}
                counts["trade"] += stat.get("rowsTrade", 0) or 0
                counts["risk"] += stat.get("rowsRisk", 0) or 0
        return counts


gateway_client = GatewayClient(settings.gateway_host, settings.gateway_port)
