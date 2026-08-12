"""
kdb_client.py - talks to gateway.q over kdb+ IPC using qpython3, so the
control API can pull live health and transit-lag numbers for the dashboard
without needing pykx or a local q install.
"""
import logging
import math
import threading
from collections.abc import Mapping
from datetime import datetime, timezone

from qpython import qconnection
from qpython.qcollection import QDictionary
from qpython.qreader import QReaderException
from qpython.qtemporal import QTemporal
from qpython.qtype import QException

from .config import settings

log = logging.getLogger("kdb_client")


class GatewayClient:
    """One shared QConnection, reused across calls - and across THREADS: the
    /metrics/snapshot route is a plain sync `def` (FastAPI runs it in a
    thread pool) and /metrics/stream additionally polls it every second via
    asyncio.to_thread, so two callers can easily land on different worker
    threads at the same moment. qpython's QConnection has no locking of its
    own - two threads calling self.q(...) concurrently were confirmed live
    to corrupt its internal state ("QConnection object has no attribute
    '_writer'", then every subsequent call failing with "gateway not
    reachable"). self._lock below serializes every call through this one
    connection; metrics polling is infrequent/low-throughput enough that
    serializing it costs nothing worth avoiding (unlike a real high-rate
    feed publisher, where a per-caller connection is worth the extra
    connections - see providers/binance.py's multi-connection sharding for
    that tradeoff instead)."""

    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.q = None
        self._lock = threading.Lock()

    def _ensure_connected(self):
        if self.q is not None:
            return
        try:
            self.q = qconnection.QConnection(host=self.host, port=self.port, pandas=False, timeout=self.timeout)
            self.q.open()
        except Exception as exc:  # noqa: BLE001
            log.warning("gateway not reachable: %s", exc)
            self.q = None

    def _call(self, expr, default):
        with self._lock:
            self._ensure_connected()
            if self.q is None:
                return default
            try:
                return self.q(expr)
            except (QException, QReaderException, ConnectionError, OSError) as exc:
                log.warning("gateway call failed (%s): %s", expr, exc)
                # Reset the IPC session and retry once for transient out-of-band messages.
                self.q = None
                self._ensure_connected()
                if self.q is None:
                    return default
                try:
                    return self.q(expr)
                except (QException, QReaderException, ConnectionError, OSError) as retry_exc:
                    log.warning("gateway retry failed (%s): %s", expr, retry_exc)
                    self.q = None
                    return default

    def _normalize(self, obj):
        if str(obj).startswith("NaT"):
            return None
        if isinstance(obj, QTemporal):
            raw = getattr(obj, "raw", None)
            if raw is not None:
                return self._normalize(raw)
            s = str(obj).split(" [metadata", 1)[0]
            return None if s == "NaT" else s
        if isinstance(obj, datetime):
            return self._format_q_timestamp(obj)
        try:
            import numpy as np
            if isinstance(obj, np.datetime64):
                if str(obj) == "NaT":
                    return None
                dt = obj.astype("datetime64[ns]").astype("int64")
                return self._format_q_timestamp(datetime.fromtimestamp(dt / 1_000_000_000, tz=timezone.utc))
            if isinstance(obj, np.generic):
                return self._normalize(obj.item())
        except Exception:  # noqa: BLE001
            pass
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="ignore")
        if isinstance(obj, float):
            return None if (math.isnan(obj) or math.isinf(obj)) else obj
        dtype = getattr(obj, "dtype", None)
        if dtype is not None and getattr(dtype, "names", None):
            names = [self._normalize(name) for name in dtype.names]
            rows = []
            for row in obj.tolist():
                if not isinstance(row, tuple):
                    row = (row,)
                rows.append({name: self._normalize(value) for name, value in zip(names, row)})
            return rows
        if isinstance(obj, QDictionary):
            items = getattr(obj, "iteritems", None)
            if callable(items):
                return {self._normalize(k): self._normalize(v) for k, v in items()}
            return {self._normalize(k): self._normalize(v) for k, v in zip(obj.keys, obj.values)}
        if isinstance(obj, Mapping):
            return {self._normalize(k): self._normalize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._normalize(x) for x in obj]
        if hasattr(obj, "tolist"):
            try:
                return self._normalize(obj.tolist())
            except Exception:  # noqa: BLE001
                return str(obj)
        return obj

    @staticmethod
    def _format_q_timestamp(ts: datetime) -> str:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts = ts.astimezone(timezone.utc)
        frac = f"{ts.microsecond:06d}" + "000"
        return ts.strftime(f"%Y.%m.%dD%H:%M:%S.{frac}")

    def health(self) -> list:
        return self._normalize(self._call(".gw.health[]", []))

    def transit_lag(self) -> list:
        rows = self._normalize(self._call(".gw.transitLag[]", []))
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, Mapping):
                    wm = row.get("watermark")
                    if isinstance(wm, int) and wm < -9_000_000_000_000_000_000:
                        row["watermark"] = None
        return rows

    def component_metrics(self) -> list:
        rows = self._normalize(self._call(".gw.componentMetrics[]", []))
        if rows and isinstance(rows, list) and isinstance(rows[0], list):
            cols = [
                "shard", "tpRecv", "tpPub", "tpQueue", "tpSubLag", "tpPubLatencyUs", "tpLogLatencyUs",
                "rdbRowsTrade", "rdbRowsRisk", "rdbReconnects", "rdbConnected",
                "wdbConnected", "wdbReconnects", "wdbLastWatermark",
            ]
            out = []
            for r in rows:
                out.append({c: (r[i] if i < len(r) else None) for i, c in enumerate(cols)})
            rows = out
        return rows

    def row_counts(self) -> dict:
        health = self.health()
        counts = {"trade": 0, "risk": 0}
        for row in health:
            for tier in ("rdb", "idb"):
                stat = row.get(tier, {}) if isinstance(row, Mapping) else {}
                if isinstance(stat, Mapping):
                    counts["trade"] += stat.get("rowsTrade", 0) or 0
                    counts["risk"] += stat.get("rowsRisk", 0) or 0
        return counts


gateway_client = GatewayClient(settings.gateway_host, settings.gateway_port, timeout=settings.gateway_timeout_sec)
