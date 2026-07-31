"""
demo_runner.py - the scripted end-to-end walkthrough, as testable logic.

The story it tells, in order:

  1. the control plane is up and you're logged in
  2. here's the whole sharded topology, all green
  3. turn the feeds on - watch ingest climb across shards
  4. CHAOS: kill a tickerplant. Watch it go down, watch the watchdog bring it
     back with zero human action, then read the heal event straight out of the
     audit trail.
  5. (optional) point the load-test's slow subscriber at it and show the
     tickerplant shed it - the fifth act, run from demokit.load_test.

Every outside effect goes through the injected `api` (a ControlApi) and the
injected `narrator`, so demokit/tests/test_demo_runner.py drives the entire
five-act structure with a fake API and asserts the runner detects the heal,
without a container in sight.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .api_client import ControlApi

RUNNING = "running"
FEED_CONNECTOR_NAMES = ("bpipe-sim", "crims-sim")


class Narrator:
    """Prints the demo beats. Colour + pauses off in tests / unattended runs."""

    def __init__(self, colour: bool = True, pause_s: float = 0.0,
                 out: Callable[[str], None] = print,
                 sleep: Callable[[float], None] = time.sleep):
        self.colour = colour
        self.pause_s = pause_s
        self._out = out
        self._sleep = sleep
        self.act = 0

    def _c(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.colour else text

    def beat(self, title: str) -> None:
        self.act += 1
        self._out("")
        self._out(self._c("1;36", f"  ── Act {self.act}: {title} ──"))

    def say(self, text: str) -> None:
        self._out(f"     {text}")

    def good(self, text: str) -> None:
        self._out("     " + self._c("1;32", "✓ " + text))

    def warn(self, text: str) -> None:
        self._out("     " + self._c("1;33", "! " + text))

    def pause(self) -> None:
        if self.pause_s > 0:
            self._sleep(self.pause_s)


@dataclass
class HealResult:
    service: str
    went_down: bool
    recovered: bool
    seconds_to_recover: float
    heal_event: dict | None = None


@dataclass
class DemoResult:
    logged_in: bool = False
    shards_seen: int = 0
    feeds_enabled: list = field(default_factory=list)
    ingest_climbed: bool = False
    heal: HealResult | None = None

    @property
    def ok(self) -> bool:
        return (self.logged_in and self.shards_seen > 0
                and self.heal is not None and self.heal.recovered)


class DemoRunner:
    def __init__(self, api: ControlApi, narrator: Narrator,
                 chaos_service: str = "tp-s0",
                 heal_timeout_s: float = 90.0, poll_s: float = 2.0,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.api = api
        self.narrator = narrator
        self.chaos_service = chaos_service
        self.heal_timeout_s = heal_timeout_s
        self.poll_s = poll_s
        self._clock = clock
        self._sleep = sleep

    # --- Act 1 -----------------------------------------------------------
    def login(self, email: str, password: str) -> bool:
        self.narrator.beat("the control plane is up")
        health = self.api.health()
        self.narrator.say(f"control-api health: {health.get('status', '?')}")
        self.api.login(email, password)
        self.narrator.good(f"logged in as {email}")
        self.narrator.pause()
        return True

    # --- Act 2 -----------------------------------------------------------
    def show_topology(self) -> int:
        self.narrator.beat("the sharded topology")
        status = self.api.topology_status()
        tps = sorted(s for s in status if s.startswith("tp-"))
        for svc in sorted(status):
            state = status[svc]
            (self.narrator.good if state == RUNNING else self.narrator.warn)(
                f"{svc:<12} {state}")
        self.narrator.say(f"{len(tps)} shards, {len(status)} managed processes")
        self.narrator.pause()
        return len(tps)

    # --- Act 3 -----------------------------------------------------------
    def enable_feeds(self) -> list:
        self.narrator.beat("turn the feeds on")
        enabled = []
        for c in self.api.list_connectors():
            if c.get("name") in FEED_CONNECTOR_NAMES and not c.get("enabled"):
                self.api.toggle_connector(c["id"])
                enabled.append(c["name"])
                self.narrator.good(f"enabled connector {c['name']}")
        if not enabled:
            self.narrator.say("feeds already enabled")
        self.narrator.pause()
        return enabled

    def watch_ingest(self, samples: int = 3, gap_s: float = 3.0) -> bool:
        """Poll metrics a few times; report whether row counts climbed."""
        self.narrator.beat("watch ingest climb")
        counts = []
        for i in range(samples):
            snap = self.api.metrics_snapshot()
            rc = snap.get("rowCounts", {}) or {}
            total = int(rc.get("trade", 0) or 0) + int(rc.get("risk", 0) or 0)
            counts.append(total)
            self.narrator.say(f"t+{i * gap_s:>4.0f}s  rows ingested: {total:,}")
            if i < samples - 1:
                self._sleep(gap_s)
        climbed = len(counts) >= 2 and counts[-1] > counts[0]
        (self.narrator.good if climbed else self.narrator.warn)(
            "ingest is climbing" if climbed else "ingest flat - is a feed running?")
        self.narrator.pause()
        return climbed

    # --- Act 4 -----------------------------------------------------------
    def chaos_kill_and_heal(self) -> HealResult:
        svc = self.chaos_service
        self.narrator.beat(f"chaos - kill {svc}, let the watchdog heal it")
        self.api.stop_service(svc)
        self.narrator.warn(f"stopped {svc} (simulating a process crash)")

        went_down = self._wait_for(lambda st: st.get(svc) != RUNNING, "down")
        if went_down:
            self.narrator.warn(f"{svc} is DOWN - no human is touching it")

        started = self._clock()
        recovered = self._wait_for(lambda st: st.get(svc) == RUNNING, "back up")
        elapsed = self._clock() - started

        heal_event = None
        if recovered:
            self.narrator.good(f"{svc} healed automatically in ~{elapsed:.0f}s")
            heal_event = self._find_heal_event(svc)
            if heal_event:
                self.narrator.good(
                    f"audit trail: {heal_event.get('actor')} → "
                    f"{heal_event.get('action')} {heal_event.get('target')} "
                    f"({heal_event.get('outcome')})")
            else:
                self.narrator.warn("healed, but no matching auto_heal audit row yet")
        else:
            self.narrator.warn(f"{svc} did NOT recover within {self.heal_timeout_s:.0f}s")
        self.narrator.pause()
        return HealResult(service=svc, went_down=went_down, recovered=recovered,
                          seconds_to_recover=elapsed, heal_event=heal_event)

    def _wait_for(self, predicate, _label: str) -> bool:
        deadline = self._clock() + self.heal_timeout_s
        while self._clock() < deadline:
            status = self.api.topology_status()
            if predicate(status):
                return True
            self._sleep(self.poll_s)
        # one last check right on the boundary
        return predicate(self.api.topology_status())

    def _find_heal_event(self, svc: str) -> dict | None:
        events = self.api.audit(limit=20, action="auto_heal") or []
        for e in events:
            if e.get("target") == svc and e.get("action") == "auto_heal":
                return e
        return None

    # --- full run --------------------------------------------------------
    def run(self, email: str, password: str,
            enable_feeds: bool = True, do_chaos: bool = True) -> DemoResult:
        result = DemoResult()
        result.logged_in = self.login(email, password)
        result.shards_seen = self.show_topology()
        if enable_feeds:
            result.feeds_enabled = self.enable_feeds()
            result.ingest_climbed = self.watch_ingest()
        if do_chaos:
            result.heal = self.chaos_kill_and_heal()

        self.narrator.beat("wrap up")
        if result.ok:
            self.narrator.good("end to end: sharded, observable, and self-healing.")
        else:
            self.narrator.warn("demo ran but not every check passed - see above.")
        return result
