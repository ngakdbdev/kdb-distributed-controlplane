"""
harness.py - the pure, dependency-free core of the load-test.

It knows nothing about kdb+, sockets, or feeds. It knows how to:

  * pace a burst of batches to hit a target rows-per-second for a fixed window,
  * count what it actually managed to publish,
  * ask a probe how many rows the data plane ingested over that window,
  * and turn the two into an honest per-step result (achieved publish rate,
    achieved ingest rate, and the loss between them).

Everything that touches the outside world - the publisher, the ingest probe,
the clock, and sleep - is injected. That is what lets `demokit/tests/` drive
the whole thing with fakes and assert the arithmetic, with no KDB-X anywhere.

The honest story this is built to tell a prospect: "at rate X the data plane
kept up (ingest ~= publish, loss ~0); past rate Y it started shedding, and
here's exactly where." A harness that only ever prints the number you asked
for isn't a load test - so this always reports the *achieved* rate, measured
two independent ways.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


class Publisher(Protocol):
    """Something that can send a batch of rows and tell you how many it sent."""

    def publish_batch(self, rows: list) -> int:  # returns rows accepted for send
        ...

    def close(self) -> None:
        ...


class RowCountProbe(Protocol):
    """Something that can report the data plane's current total ingested rows."""

    def total_rows(self) -> int:
        ...


BatchGenerator = Callable[[int], list]  # n -> list of n rows


@dataclass(frozen=True)
class RateStep:
    """One rung of the load ramp: hold `target_rps` for `duration_s`."""

    target_rps: int
    duration_s: float
    batch_ms: int = 100  # how often we flush a batch within the step

    def __post_init__(self):
        if self.target_rps <= 0:
            raise ValueError(f"target_rps must be > 0, got {self.target_rps}")
        if self.duration_s <= 0:
            raise ValueError(f"duration_s must be > 0, got {self.duration_s}")
        if not (0 < self.batch_ms <= 10_000):
            raise ValueError(f"batch_ms must be in (0, 10000], got {self.batch_ms}")

    @property
    def batches(self) -> int:
        return max(1, int(round(self.duration_s * 1000.0 / self.batch_ms)))

    @property
    def rows_per_batch(self) -> int:
        return max(1, int(round(self.target_rps * self.batch_ms / 1000.0)))


@dataclass
class StepResult:
    target_rps: int
    duration_s: float
    published: int
    ingested: int
    wall_s: float

    @property
    def achieved_publish_rps(self) -> float:
        return self.published / self.wall_s if self.wall_s > 0 else 0.0

    @property
    def achieved_ingest_rps(self) -> float:
        return self.ingested / self.wall_s if self.wall_s > 0 else 0.0

    @property
    def loss_pct(self) -> float:
        """Fraction of published rows the data plane did NOT ingest, as a %.

        Clamped at 0: a probe can legitimately report a slightly *higher*
        delta than we published (rows still draining from a prior step land
        inside this window), and negative "loss" would just be confusing.
        """
        if self.published <= 0:
            return 0.0
        return max(0.0, 100.0 * (self.published - self.ingested) / self.published)

    @property
    def kept_up(self) -> bool:
        """Did the data plane stay within 2% of the offered load this step?"""
        return self.loss_pct <= 2.0


@dataclass
class LoadReport:
    steps: list[StepResult] = field(default_factory=list)

    @property
    def total_published(self) -> int:
        return sum(s.published for s in self.steps)

    @property
    def total_ingested(self) -> int:
        return sum(s.ingested for s in self.steps)

    @property
    def peak_sustained_rps(self) -> float:
        """Highest achieved *ingest* rate among steps that kept up.

        This is the number worth quoting: the fastest the data plane actually
        absorbed load without shedding, not the fastest we threw at it.
        """
        sustained = [s.achieved_ingest_rps for s in self.steps if s.kept_up]
        return max(sustained) if sustained else 0.0

    @property
    def first_shed_step(self) -> StepResult | None:
        """The first step where the data plane started dropping (loss > 2%)."""
        for s in self.steps:
            if not s.kept_up:
                return s
        return None


def run_profile(
    steps: list[RateStep],
    publisher: Publisher,
    probe: RowCountProbe,
    generate_batch: BatchGenerator,
    on_step: Callable[[StepResult], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> LoadReport:
    """Run every rung of the ramp and return a per-step report.

    Injecting `clock`/`sleep` (and fake publisher/probe/generator) is what
    makes this deterministic and unit-testable - see demokit/tests.
    """
    report = LoadReport()
    for step in steps:
        interval = step.batch_ms / 1000.0
        rows_each = step.rows_per_batch

        ingest_before = probe.total_rows()
        published = 0
        started = clock()
        for _ in range(step.batches):
            batch_start = clock()
            rows = generate_batch(rows_each)
            published += publisher.publish_batch(rows)
            # pace: sleep off whatever's left of this batch's slot
            drift = interval - (clock() - batch_start)
            if drift > 0:
                sleep(drift)
        wall = clock() - started
        # let the last in-flight batch land before we read the ingest side
        sleep(min(interval, 0.5))
        ingest_after = probe.total_rows()

        result = StepResult(
            target_rps=step.target_rps,
            duration_s=step.duration_s,
            published=published,
            ingested=max(0, ingest_after - ingest_before),
            wall_s=wall,
        )
        report.steps.append(result)
        if on_step is not None:
            on_step(result)
    return report


def ramp(start_rps: int, stop_rps: int, step_rps: int,
         duration_s: float, batch_ms: int = 100) -> list[RateStep]:
    """Convenience: build an inclusive ramp of RateSteps.

    ramp(1000, 5000, 1000, 20) -> steps at 1k,2k,3k,4k,5k rps, 20s each.
    """
    if step_rps <= 0:
        raise ValueError("step_rps must be > 0")
    out: list[RateStep] = []
    r = start_rps
    while r <= stop_rps:
        out.append(RateStep(target_rps=r, duration_s=duration_s, batch_ms=batch_ms))
        r += step_rps
    return out
