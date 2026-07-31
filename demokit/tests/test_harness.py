"""
Unit tests for demokit.harness - the pure accounting core.

No kdb+, no sockets: fakes for publisher/probe/clock/sleep let us assert the
arithmetic exactly. This is what makes the load-test's numbers trustworthy -
the measurement logic is pinned down here even though the throughput itself
can only be produced on a real deployment.
"""
import pytest

from demokit import harness
from demokit.harness import RateStep, StepResult, LoadReport, run_profile, ramp


# ---- fakes ---------------------------------------------------------------

class FakePublisher:
    """Counts everything handed to it; shares a running total with a probe."""

    def __init__(self, sink: list):
        self.sink = sink  # sink[0] = cumulative published

    def publish_batch(self, rows):
        self.sink[0] += len(rows)
        return len(rows)

    def close(self):
        pass


class FakeProbe:
    """Reports keep_fraction of everything the publisher has sent so far."""

    def __init__(self, sink: list, keep_fraction: float):
        self.sink = sink
        self.keep = keep_fraction

    def total_rows(self):
        return int(self.keep * self.sink[0])


def virtual_time():
    """A clock+sleep pair where sleep advances the clock deterministically."""
    t = [0.0]
    return (lambda: t[0]), (lambda s: t.__setitem__(0, t[0] + s))


def const_gen(n):
    return [("row",)] * n


# ---- RateStep ------------------------------------------------------------

def test_ratestep_rows_and_batches():
    s = RateStep(target_rps=1000, duration_s=2.0, batch_ms=100)
    assert s.batches == 20            # 2s / 100ms
    assert s.rows_per_batch == 100    # 1000 rps * 0.1s

def test_ratestep_rejects_bad_values():
    with pytest.raises(ValueError):
        RateStep(target_rps=0, duration_s=1.0)
    with pytest.raises(ValueError):
        RateStep(target_rps=10, duration_s=0)
    with pytest.raises(ValueError):
        RateStep(target_rps=10, duration_s=1.0, batch_ms=0)


# ---- StepResult math -----------------------------------------------------

def test_stepresult_rates_and_zero_loss():
    r = StepResult(target_rps=1000, duration_s=2.0, published=2000,
                   ingested=2000, wall_s=2.0)
    assert r.achieved_publish_rps == 1000
    assert r.achieved_ingest_rps == 1000
    assert r.loss_pct == 0.0
    assert r.kept_up is True

def test_stepresult_loss_when_ingest_lags():
    r = StepResult(target_rps=1000, duration_s=2.0, published=2000,
                   ingested=1800, wall_s=2.0)
    assert r.loss_pct == pytest.approx(10.0)
    assert r.kept_up is False           # 10% > 2% threshold

def test_stepresult_loss_clamped_at_zero():
    # probe can report a higher delta than we published (drain from prior step)
    r = StepResult(target_rps=1000, duration_s=1.0, published=1000,
                   ingested=1050, wall_s=1.0)
    assert r.loss_pct == 0.0

def test_stepresult_two_percent_boundary_keeps_up():
    r = StepResult(target_rps=1000, duration_s=1.0, published=1000,
                   ingested=980, wall_s=1.0)
    assert r.loss_pct == pytest.approx(2.0)
    assert r.kept_up is True             # exactly 2% still counts as keeping up


# ---- LoadReport aggregation ---------------------------------------------

def test_report_peak_sustained_and_first_shed():
    report = LoadReport(steps=[
        StepResult(1000, 1.0, 1000, 1000, 1.0),   # ok, ingest 1000
        StepResult(2000, 1.0, 2000, 2000, 1.0),   # ok, ingest 2000
        StepResult(3000, 1.0, 3000, 2400, 1.0),   # SHED (20% loss)
        StepResult(4000, 1.0, 4000, 2500, 1.0),   # SHED
    ])
    # peak sustained is the fastest ingest among steps that kept up (2000)
    assert report.peak_sustained_rps == pytest.approx(2000)
    shed = report.first_shed_step
    assert shed is not None and shed.target_rps == 3000
    assert report.total_published == 10000

def test_report_no_shed_returns_none():
    report = LoadReport(steps=[StepResult(1000, 1.0, 1000, 1000, 1.0)])
    assert report.first_shed_step is None
    assert report.peak_sustained_rps == pytest.approx(1000)


# ---- run_profile end to end ---------------------------------------------

def test_run_profile_counts_published_and_measures_loss():
    sink = [0]
    pub = FakePublisher(sink)
    probe = FakeProbe(sink, keep_fraction=0.9)   # data plane keeps 90%
    clock, sleep = virtual_time()
    steps = [RateStep(target_rps=1000, duration_s=1.0, batch_ms=100)]  # 10 batches * 100 rows

    report = run_profile(steps, pub, probe, const_gen, clock=clock, sleep=sleep)

    assert len(report.steps) == 1
    step = report.steps[0]
    assert step.published == 1000            # 10 batches * 100 rows
    assert step.ingested == pytest.approx(900, abs=1)
    assert step.loss_pct == pytest.approx(10.0, abs=0.5)
    assert step.kept_up is False
    # wall clock advanced by the paced sleeps, so rates are finite and positive
    assert step.achieved_publish_rps > 0

def test_run_profile_multistep_perfect_keep():
    sink = [0]
    pub = FakePublisher(sink)
    probe = FakeProbe(sink, keep_fraction=1.0)
    clock, sleep = virtual_time()
    steps = ramp(1000, 2000, 1000, duration_s=1.0, batch_ms=100)  # 1k then 2k

    report = run_profile(steps, pub, probe, const_gen, clock=clock, sleep=sleep)

    assert [s.target_rps for s in report.steps] == [1000, 2000]
    assert report.steps[0].published == 1000
    assert report.steps[1].published == 2000
    assert all(s.kept_up for s in report.steps)
    assert report.first_shed_step is None


# ---- ramp helper ---------------------------------------------------------

def test_ramp_is_inclusive_and_ordered():
    steps = ramp(1000, 5000, 1000, duration_s=10.0)
    assert [s.target_rps for s in steps] == [1000, 2000, 3000, 4000, 5000]
    assert all(s.duration_s == 10.0 for s in steps)

def test_ramp_rejects_nonpositive_step():
    with pytest.raises(ValueError):
        ramp(1000, 5000, 0, duration_s=10.0)
