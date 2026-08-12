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
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from qpython import qconnection
from qpython.qcollection import qlist, qtable
from qpython.qtype import QDOUBLE_LIST, QException, QLONG_LIST, QSYMBOL_LIST, QTIMESTAMP_LIST

import topology

# column order + q vector type for each table's binary publish - must match
# data-plane/q/schema.q exactly (kdb+ "float" is 64-bit, i.e. qpython's
# QDOUBLE_LIST; qpython's own QFLOAT_LIST is kdb+'s 32-bit "real", unused here)
_TABLE_SPECS = {
    "trade": (
        ("time", "timestamp"), ("sym", "symbol"), ("price", "float"),
        ("size", "long"), ("side", "symbol"), ("venue", "symbol"), ("shard", "symbol"),
    ),
    "risk": (
        ("time", "timestamp"), ("sym", "symbol"), ("riskType", "symbol"), ("limit", "float"),
        ("exposure", "float"), ("status", "symbol"), ("shard", "symbol"),
    ),
}

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

    def _normalize_value(self, value):
        if isinstance(value, datetime):
            return self._fmt_ts(value)
        return value

    def _normalize_row(self, row: list) -> list:
        return [self._normalize_value(v) for v in row]

    def publish(self, table: str, rows: list):
        """Fire-and-forget async publish of a batch of rows to `table`."""
        if self.q is None:
            self.connect()
        try:
            self._publish_binary(table, rows)
        except Exception as exc:  # noqa: BLE001 - any failure -> log + reconnect, never die
            self.log.warning("publish to %s:%s failed (%s: %s), reconnecting",
                             self.host, self.port, type(exc).__name__, exc)
            self.q = None
            self.connect()

    @staticmethod
    def _fmt_ts(val) -> str:
        if isinstance(val, datetime):
            if val.tzinfo is not None:
                val = val.astimezone(timezone.utc).replace(tzinfo=None)
            return val.strftime("%Y.%m.%dD%H:%M:%S.%f") + "000"
        return str(val)

    @staticmethod
    def _to_datetime64(val) -> np.datetime64:
        if isinstance(val, datetime):
            if val.tzinfo is not None:
                val = val.astimezone(timezone.utc).replace(tzinfo=None)
        return np.datetime64(val, "ns")

    def _publish_binary(self, table: str, rows: list) -> None:
        """Send a batch as a real kdb+ table over IPC (type 98h), matching what
        tick.q's .u.doUpd already fast-paths (`d:$[98h=type data;data;...]`) -
        no text round-trip, no q-side parsing, just binary deserialize + insert."""
        if not rows:
            return
        spec = _TABLE_SPECS.get(table)
        if spec is None:
            raise ValueError(f"unsupported table for binary publish: {table}")

        cols = list(zip(*rows))
        col_names = []
        col_data = []
        for i, (name, qtype) in enumerate(spec):
            values = cols[i]  # raw values - _normalize_value's string coercion is for the old text path only
            if qtype == "timestamp":
                arr = np.array([self._to_datetime64(v) for v in values], dtype="datetime64[ns]")
                col_data.append(qlist(arr, qtype=QTIMESTAMP_LIST))
            elif qtype == "symbol":
                arr = np.array([str(v) for v in values])
                col_data.append(qlist(arr, qtype=QSYMBOL_LIST))
            elif qtype == "float":
                arr = np.array(values, dtype=np.float64)
                col_data.append(qlist(arr, qtype=QDOUBLE_LIST))
            elif qtype == "long":
                arr = np.array(values, dtype=np.int64)
                col_data.append(qlist(arr, qtype=QLONG_LIST))
            else:
                raise ValueError(f"unsupported qtype: {qtype}")
            col_names.append(name)

        tbl = qtable(col_names, col_data)
        self.q.sendAsync(".u.upd", np.string_(table), tbl)

    def close(self):
        if self.q is not None:
            self.q.close()


class ShardedPublisher:
    """Fans a feed's rows across the N per-shard tickerplants.

    Replaces the old hardcoded two-connection (A_M / N_Z) setup: it opens one
    TickerplantConnection per shard (`tp-s0`, `tp-s1`, ...), and for each
    batch groups rows by the shard tag the generator already stamped on them
    (via topology.shard_of), publishing each group to the matching TP. Change
    SHARD_COUNT and the feed fans out to that many tickerplants with no code
    change - the feed side of "N-way sharding is real".
    """

    def __init__(self, feed_name: str, shard_count: Optional[int] = None,
                 host_pattern: Optional[str] = None, port: Optional[int] = None,
                 shard_index: int = -1):
        self.n = shard_count if shard_count is not None else int(os.environ.get("SHARD_COUNT", "2"))
        host_pattern = host_pattern or os.environ.get("TP_HOST_PATTERN", "tp-{shard}")
        port = port if port is not None else int(os.environ.get("TP_PORT", str(topology.TIER_PORTS["tickerplant"])))
        self.shard_index = shard_index
        self.log = logging.getLogger(feed_name)
        self.conns = {
            s.id: TickerplantConnection(host_pattern.format(shard=s.id), port, f"{feed_name}.{s.id}")
            for s in topology.shards(self.n)
        }
        self.log.info("sharded publisher: %d shards -> %s",
                      self.n, ", ".join(host_pattern.format(shard=s.id) for s in topology.shards(self.n)))

    def connect(self):
        for c in self.conns.values():
            c.connect()

    def publish_rows(self, table: str, rows: list):
        """Group rows by their shard tag and publish each group to its TP."""
        batches: dict[str, list] = defaultdict(list)
        for r in rows:
            batches[r[self.shard_index]].append(r)
        for shard_id, batch in batches.items():
            conn = self.conns.get(shard_id)
            if conn is None:  # shard tag not in this topology - shouldn't happen
                self.log.warning("no connection for shard %s, dropping %d rows", shard_id, len(batch))
                continue
            conn.publish(table, batch)

    def close(self):
        for c in self.conns.values():
            c.close()


def build_universe(count: int, symbols_arg: str, symbols_file: str, builtin: list) -> list:
    """Resolve a feed's symbol universe: an explicit list/file if given (this is
    the connector "symbol group" override - see routers/connectors.py), otherwise
    `builtin`, padded with synthetic tickers (SYN00001, ...) up to `count` so you
    can simulate thousands of symbols for high-cardinality load tests. Shared by
    bpipe_sim.py and crims_sim.py so a symbol-group override behaves identically
    across feed kinds.

    `count` padding only applies to the builtin default - an explicit override
    means "feed exactly this group", full stop. Padding it with thousands of
    unrelated SYN##### tickers would silently defeat the whole point of scoping
    a connector to a specific symbol group."""
    explicit = False
    if symbols_file and os.path.exists(symbols_file):
        with open(symbols_file) as fh:
            syms = [s.strip().upper() for s in fh.read().replace("\n", ",").split(",") if s.strip()]
        explicit = True
    elif symbols_arg:
        syms = [s.strip().upper() for s in symbols_arg.split(",") if s.strip()]
        explicit = True
    else:
        syms = list(builtin)
    if not explicit and count and count > 0:
        if len(syms) >= count:
            syms = syms[:count]
        else:
            # Cycle the leading letter A-Z as we pad. topology.shard_of
            # partitions purely by a symbol's first letter (contiguous
            # ranges, e.g. A-M/N-Z at SHARD_COUNT=2) - the same table
            # gateway.q routes with - so an all-"SYN#####" universe dumps
            # every synthetic symbol on whichever single shard owns "S".
            # Confirmed live: at SIM_SYMBOL_COUNT=1000 that put ~98% of
            # traffic on one shard's tickerplant/RDB and destabilized it
            # under load. Cycling the prefix through all 26 letters spreads
            # the padding evenly across however many shards are configured.
            i = 1
            while len(syms) < count:
                letter = topology.ALPHABET[(i - 1) % len(topology.ALPHABET)]
                syms.append(f"{letter}SYN{i:05d}")
                i += 1
    seen = set()
    return [s for s in syms if not (s in seen or seen.add(s))]


def now_ns() -> int:
    """kdb+ timestamp is nanoseconds since 2000-01-01; qpython handles the
    epoch offset for us if we hand it a Python datetime, so callers should
    prefer utc_now() below over calling this directly."""
    return int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
