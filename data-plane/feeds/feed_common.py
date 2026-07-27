"""
feed_common.py - shared kdb+ IPC connection helper for the feed simulators.

Uses qpython3 (pure-Python implementation of the kdb+ IPC protocol) so the
feed containers don't need a q/KDB-X install of their own - only the
tickerplant and downstream processes do. This mirrors how real feed handlers
are typically deployed in production: written in Java/Python/C++, not q.
"""
import logging
import os
import time
from datetime import datetime, timezone

from qpython import qconnection
from qpython.qtype import QException

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


class TickerplantConnection:
    """Reconnecting IPC client to a tickerplant, used by every feed sim."""

    def __init__(self, host: str, port: int, name: str):
        self.host = host
        self.port = port
        self.name = name
        self.log = logging.getLogger(name)
        self.q = None

    def connect(self):
        while self.q is None:
            try:
                self.q = qconnection.QConnection(host=self.host, port=self.port, pandas=False)
                self.q.open()
                self.log.info("connected to tickerplant at %s:%s", self.host, self.port)
            except Exception as exc:  # noqa: BLE001 - reconnect loop, log and retry
                self.log.warning("tickerplant not reachable yet (%s), retrying in 2s", exc)
                self.q = None
                time.sleep(2)

    def publish(self, table: str, rows: list):
        """Fire-and-forget async publish of a batch of rows to `table`."""
        if self.q is None:
            self.connect()
        try:
            # .u.upd is called async (no return value expected) so the feed
            # never blocks on the tickerplant's ack, matching production
            # feedhandler behaviour
            self.q(".u.upd", table, rows, sync=False)
        except (QException, ConnectionError, OSError) as exc:
            self.log.warning("publish failed (%s), reconnecting", exc)
            self.q = None
            self.connect()

    def close(self):
        if self.q is not None:
            self.q.close()


def now_ns() -> int:
    """kdb+ timestamp is nanoseconds since 2000-01-01; qpython handles the
    epoch offset for us if we hand it a Python datetime, so callers should
    prefer utc_now() below over calling this directly."""
    return int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
