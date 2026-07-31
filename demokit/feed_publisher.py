"""
feed_publisher.py - the live publisher the load-test drives, and the
deliberately-slow subscriber the discard demo drives.

Both reuse the *actual* feed code in data-plane/feeds (ShardedPublisher,
topology, the bpipe trade generator) rather than reimplementing the publish
path. That's deliberate: a load-test that exercises a parallel code path only
proves the parallel code path. This one hammers the same fan-out-across-shards
publisher a real feed handler uses, so the throughput number reflects the
system you'd actually ship.

Kept out of demokit.harness so the harness stays pure/testable; this module
is thin glue and only imports at call time.
"""
from __future__ import annotations

import os
import sys
import time
import logging

log = logging.getLogger("demokit.feed_publisher")


def _add_feeds_to_path() -> None:
    """Put data-plane/feeds on sys.path so we can import the real feed code."""
    here = os.path.dirname(os.path.abspath(__file__))
    feeds = os.path.normpath(os.path.join(here, "..", "data-plane", "feeds"))
    if feeds not in sys.path:
        sys.path.insert(0, feeds)


class FeedPublisher:
    """harness.Publisher backed by the real ShardedPublisher fan-out.

    publish_batch(rows) sends a pre-generated batch of `trade` rows, grouping
    them across the per-shard tickerplants exactly as bpipe_sim does. Returns
    the number of rows handed off (so the harness counts what left the client).
    """

    def __init__(self, shard_count: int, tp_host_pattern: str | None = None,
                 tp_port: int | None = None):
        _add_feeds_to_path()
        from feed_common import ShardedPublisher  # noqa: E402
        self.shard_count = shard_count
        self.pub = ShardedPublisher(
            "loadtest", shard_count=shard_count,
            host_pattern=tp_host_pattern, port=tp_port, shard_index=-1,
        )
        self.pub.connect()

    def publish_batch(self, rows: list) -> int:
        if not rows:
            return 0
        self.pub.publish_rows("trade", rows)
        return len(rows)

    def close(self):
        self.pub.close()


def make_trade_generator(shard_count: int):
    """Return a harness BatchGenerator: n -> n synthetic trade rows.

    Reuses bpipe_sim.gen_trade so the rows are byte-for-byte what the real
    feed simulator produces (including the shard tag topology.shard_of stamps
    on, which is what routes them and what the gateway re-derives).
    """
    _add_feeds_to_path()
    import random
    import bpipe_sim  # noqa: E402

    prices = {s: random.uniform(20, 500) for s in bpipe_sim.SYMBOLS}

    def generate(n: int) -> list:
        batch = []
        for _ in range(n):
            sym = random.choice(bpipe_sim.SYMBOLS)
            prices[sym] *= 1 + random.uniform(-0.0015, 0.0015)
            batch.append(bpipe_sim.gen_trade(sym, prices[sym], shard_count))
        return batch

    return generate


class SlowSubscriber:
    """A subscriber that connects to a tickerplant and then reads too slowly
    on purpose, so its outbound queue on the TP grows past SLOW_SUB_MAX_BYTES
    and tick.q's strike-based auto-discard drops it.

    This is the antagonist in the slow-subscriber-discard demo: run it, then
    watch TickerplantSubProbe show its queued bytes climb, strikes accrue, and
    finally the connection appear in `.u.discarded`. Proves the feature end to
    end instead of asserting it in a slide.
    """

    def __init__(self, tp_host: str, tp_port: int, read_every_s: float = 5.0):
        self.tp_host = tp_host
        self.tp_port = tp_port
        self.read_every_s = read_every_s
        self.q = None

    def run(self, max_seconds: float = 120.0) -> None:
        from qpython import qconnection
        self.q = qconnection.QConnection(host=self.tp_host, port=self.tp_port, pandas=False)
        self.q.open()
        # subscribe to everything, then deliberately stall: we ask the TP to
        # push us all trades but only service the socket every read_every_s,
        # so the TP's per-handle outbound queue backs up on our behalf.
        self.q.sendSync(".u.sub", "trade", "")
        log.info("slow subscriber attached to %s:%s, reading every %.1fs",
                 self.tp_host, self.tp_port, self.read_every_s)
        deadline = time.monotonic() + max_seconds
        while time.monotonic() < deadline:
            time.sleep(self.read_every_s)
            try:
                # a token, infrequent read - nowhere near fast enough to drain
                self.q.receive(data_only=False, raw=False)
            except Exception:  # noqa: BLE001 - we EXPECT to be dropped
                log.info("slow subscriber was discarded by the tickerplant (as designed)")
                return
        log.info("slow subscriber finished its window without being dropped")

    def close(self):
        if self.q is not None:
            try:
                self.q.close()
            finally:
                self.q = None
