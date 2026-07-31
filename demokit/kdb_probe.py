"""
kdb_probe.py - the live side of the load-test: read the data plane's state
over kdb+ IPC. Mirrors control-api/app/kdb_client.py so the harness measures
ingest exactly the way the dashboard does, not via some parallel path that
could disagree with what an operator sees on screen.

Nothing here runs in the sandbox (there's no KDB-X), so it stays thin: all
the arithmetic that CAN be tested lives in demokit/harness.py behind the
RowCountProbe protocol these classes satisfy.
"""
from __future__ import annotations

import logging

log = logging.getLogger("demokit.kdb_probe")


def _connect(host: str, port: int):
    from qpython import qconnection  # imported lazily so import-time needs no q
    conn = qconnection.QConnection(host=host, port=port, pandas=False)
    conn.open()
    return conn


class GatewayRowCountProbe:
    """total_rows() = trade+risk rows currently held across all shards' RDB+IDB.

    Reads `.gw.health[]` from gateway.q and sums the per-tier row counts, the
    same figures control-api/app/kdb_client.GatewayClient.row_counts() returns.
    Reconnects on failure so a mid-test gateway restart (e.g. the chaos step)
    doesn't abort the run - it just reads 0 until the gateway is back.
    """

    def __init__(self, host: str = "localhost", port: int = 5050,
                 tables: tuple[str, ...] = ("trade", "risk")):
        self.host = host
        self.port = port
        self.tables = tables
        self.q = None

    def _ensure(self):
        if self.q is not None:
            return
        try:
            self.q = _connect(self.host, self.port)
        except Exception as exc:  # noqa: BLE001 - reconnect loop
            log.warning("gateway %s:%s not reachable: %s", self.host, self.port, exc)
            self.q = None

    def total_rows(self) -> int:
        self._ensure()
        if self.q is None:
            return 0
        try:
            health = self.q(".gw.health[]")
        except Exception as exc:  # noqa: BLE001
            log.warning("gateway health call failed (%s), will reconnect", exc)
            self.q = None
            return 0
        total = 0
        for row in health or []:
            if not isinstance(row, dict):
                continue
            for tier in ("rdb", "idb"):
                stat = row.get(tier, {}) or {}
                if not isinstance(stat, dict):
                    continue
                total += int(stat.get("rowsTrade", 0) or 0)
                total += int(stat.get("rowsRisk", 0) or 0)
        return total

    def close(self):
        if self.q is not None:
            try:
                self.q.close()
            finally:
                self.q = None


class TickerplantSubProbe:
    """Reads a single tickerplant's subscriber accounting for the slow-sub demo.

    `.u.subStats[]` gives per-subscriber queued bytes + strike count;
    `.u.discarded` is the audit table of connections tick.q has auto-dropped.
    Used to *prove* the slow-subscriber-discard feature with numbers rather
    than a claim: watch queued bytes climb, strikes accrue, then the row move
    into `.u.discarded`.
    """

    def __init__(self, host: str = "localhost", port: int = 5010):
        self.host = host
        self.port = port
        self.q = None

    def _ensure(self):
        if self.q is None:
            self.q = _connect(self.host, self.port)

    def sub_stats(self) -> list:
        self._ensure()
        try:
            return self.q(".u.subStats[]") or []
        except Exception as exc:  # noqa: BLE001
            log.warning("subStats call failed: %s", exc)
            self.q = None
            return []

    def discarded_count(self) -> int:
        self._ensure()
        try:
            return int(self.q("count .u.discarded"))
        except Exception as exc:  # noqa: BLE001
            log.warning("discarded count failed: %s", exc)
            self.q = None
            return 0

    def close(self):
        if self.q is not None:
            try:
                self.q.close()
            finally:
                self.q = None
