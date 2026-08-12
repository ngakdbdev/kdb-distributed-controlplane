"""Tests for kdb_client.GatewayClient - in particular its thread-safety.

/metrics/snapshot is a plain sync route (FastAPI runs it in a thread pool)
and /metrics/stream additionally polls the SAME shared gateway_client every
second via asyncio.to_thread - two callers can land on different threads at
the same moment. Confirmed live: without locking, concurrent calls corrupted
qpython's QConnection ("QConnection object has no attribute '_writer'"),
after which every subsequent call failed with "gateway not reachable" until
the process restarted. These tests fake the connection layer (no real q
process) and drive real concurrent threads through the real GatewayClient
code to prove the lock actually serializes access."""
import threading
import time

from app.kdb_client import GatewayClient


class _SlowFakeConn:
    """Simulates a qpython QConnection that takes a moment to answer - long
    enough that two threads calling it without a lock would overlap."""

    def __init__(self, delay=0.02):
        self.delay = delay
        self.in_flight = 0
        self.max_concurrent = 0
        self._open = True

    def __call__(self, expr):
        self.in_flight += 1
        self.max_concurrent = max(self.max_concurrent, self.in_flight)
        time.sleep(self.delay)
        self.in_flight -= 1
        return f"result for {expr}"


def _client_with_fake_conn(fake):
    client = GatewayClient("fake-host", 1234, timeout=1.0)
    client.q = fake
    client._ensure_connected = lambda: None  # already "connected" - skip the real network path
    return client


def test_single_threaded_call_returns_result():
    fake = _SlowFakeConn(delay=0)
    client = _client_with_fake_conn(fake)
    assert client._call(".gw.health[]", []) == "result for .gw.health[]"


def test_concurrent_calls_never_overlap_on_the_shared_connection():
    """The actual regression test: N threads hitting _call at once must be
    serialized (max_concurrent stays at 1), not racing the same connection
    object the way the real bug did."""
    fake = _SlowFakeConn(delay=0.03)
    client = _client_with_fake_conn(fake)

    results = []
    def worker():
        results.append(client._call(".gw.health[]", "default"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert fake.max_concurrent == 1          # never more than one call in flight
    assert len(results) == 8
    assert all(r == "result for .gw.health[]" for r in results)


def test_call_failure_resets_connection_and_returns_default():
    class BoomThenFail:
        def __call__(self, expr):
            raise ConnectionError("gateway hung up")

    client = _client_with_fake_conn(BoomThenFail())
    client._ensure_connected = lambda: None  # keep failing - simulates gateway staying down
    assert client._call(".gw.health[]", "default") == "default"
