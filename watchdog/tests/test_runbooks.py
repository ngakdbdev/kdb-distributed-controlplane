"""Tests for the multi-runbook self-healing watchdog.

These exercise flapping detection, failure-signature classification, each
runbook, and a full check_and_heal pass against a fake orchestrator - no
docker/k8s and no real sleeps, so it runs anywhere in well under a second.
"""
import itertools

import pytest

import flap as flap_mod
import runbooks as R


# --------------------------------------------------------------- fake orchestrator
class FakeOrchestrator:
    """Minimal stand-in: a mutable {service: status} map. start() moves a
    service to whatever start_result says (default 'running')."""

    def __init__(self, statuses):
        self.statuses = dict(statuses)
        self.start_result = {}          # service -> status after start()
        self.starts = []                # log of start() calls

    def status(self, service):
        return self.statuses.get(service, "not_found")

    def start(self, service):
        self.starts.append(service)
        self.statuses[service] = self.start_result.get(service, "running")
        return True


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


# --------------------------------------------------------------- FlapTracker
def test_flap_threshold_within_window():
    clk = FakeClock()
    ft = flap_mod.FlapTracker(window_sec=100, threshold=3, cooldown_sec=300, clock=clk)
    ft.record_restart("rdb-s0"); clk.advance(10)
    ft.record_restart("rdb-s0"); clk.advance(10)
    assert not ft.is_flapping("rdb-s0")          # 2 within window
    ft.record_restart("rdb-s0")
    assert ft.is_flapping("rdb-s0")              # 3 within window


def test_flap_window_prunes_old_restarts():
    clk = FakeClock()
    ft = flap_mod.FlapTracker(window_sec=100, threshold=3, clock=clk)
    ft.record_restart("x"); clk.advance(60)
    ft.record_restart("x"); clk.advance(60)      # first now 120s old -> pruned
    ft.record_restart("x")
    assert ft.restart_count("x") == 2            # only the last two are in-window
    assert not ft.is_flapping("x")


def test_cooldown_blocks_then_expires_and_resets():
    clk = FakeClock()
    ft = flap_mod.FlapTracker(window_sec=100, threshold=3, cooldown_sec=300, clock=clk)
    for _ in range(3):
        ft.record_restart("gw")
    assert ft.is_flapping("gw")
    ft.start_cooldown("gw")
    assert ft.in_cooldown("gw")
    clk.advance(299)
    assert ft.in_cooldown("gw")
    clk.advance(2)                               # past cooldown
    assert not ft.in_cooldown("gw")
    assert ft.restart_count("gw") == 0           # history wiped -> fresh slate


# --------------------------------------------------------------- dependency map
@pytest.mark.parametrize("svc,dep", [
    ("wdb-s0", "tp-s0"), ("rdb-s3", "tp-s3"), ("idb-s12", "tp-s12"),
    ("tp-s0", None), ("gateway", None), ("bpipe-sim", None),
])
def test_tickerplant_dependency(svc, dep):
    assert R.tickerplant_dependency(svc) == dep


# --------------------------------------------------------------- classify
def _ft():
    return flap_mod.FlapTracker(clock=FakeClock())


def test_classify_running_is_healthy():
    assert R.classify("rdb-s0", {"rdb-s0": "running"}, _ft()) == R.HEALTHY


def test_classify_running_but_unhealthy():
    sig = R.classify("rdb-s0", {"rdb-s0": "running", "tp-s0": "running"}, _ft(), healthy=False)
    assert sig == R.CONTAINER_UNHEALTHY


def test_classify_container_down():
    sig = R.classify("tp-s0", {"tp-s0": "exited"}, _ft())
    assert sig == R.CONTAINER_DOWN


def test_classify_dependency_down_beats_container_down():
    sm = {"rdb-s0": "exited", "tp-s0": "exited"}
    assert R.classify("rdb-s0", sm, _ft()) == R.DEPENDENCY_DOWN


def test_classify_plain_down_when_tp_up():
    sm = {"rdb-s0": "exited", "tp-s0": "running"}
    assert R.classify("rdb-s0", sm, _ft()) == R.CONTAINER_DOWN


def test_classify_flapping_when_tp_up():
    ft = _ft()
    for _ in range(3):
        ft.record_restart("rdb-s0")
    sm = {"rdb-s0": "exited", "tp-s0": "running"}
    assert R.classify("rdb-s0", sm, ft) == R.FLAPPING


def test_classify_dependency_down_beats_flapping():
    # root cause is the tp being down; don't escalate the dependent
    ft = _ft()
    for _ in range(5):
        ft.record_restart("rdb-s0")
    sm = {"rdb-s0": "exited", "tp-s0": "exited"}
    assert R.classify("rdb-s0", sm, ft) == R.DEPENDENCY_DOWN


# --------------------------------------------------------------- runbooks
def test_restart_and_verify_success_records_flap():
    orch = FakeOrchestrator({"tp-s0": "exited"})
    ft = _ft()
    res = R.restart_and_verify(orch, "tp-s0", {"flap": ft, "recovery_delay_sec": 0})
    assert res["outcome"] == "success" and res["final_status"] == "running"
    assert orch.starts == ["tp-s0"]
    assert ft.restart_count("tp-s0") == 1        # incident counted for flap detection


def test_restart_and_verify_failure_after_max_attempts():
    orch = FakeOrchestrator({"tp-s0": "exited"})
    orch.start_result["tp-s0"] = "exited"        # never recovers
    ft = _ft()
    res = R.restart_and_verify(orch, "tp-s0", {"flap": ft, "recovery_delay_sec": 0})
    assert res["outcome"] == "failure"
    assert res["attempts"] == R.MAX_RESTART_ATTEMPTS
    assert len(orch.starts) == R.MAX_RESTART_ATTEMPTS


def test_defer_to_dependency_does_not_restart():
    orch = FakeOrchestrator({"rdb-s0": "exited", "tp-s0": "exited"})
    res = R.defer_to_dependency(orch, "rdb-s0", {"flap": _ft(), "status_map": orch.statuses})
    assert res["outcome"] == "deferred"
    assert "tp-s0" in res["detail"]
    assert orch.starts == []                     # crucially, no restart of the dependent


def test_escalate_opens_cooldown_and_does_not_restart():
    orch = FakeOrchestrator({"gateway": "exited"})
    ft = _ft()
    for _ in range(3):
        ft.record_restart("gateway")
    res = R.escalate(orch, "gateway", {"flap": ft, "status_map": orch.statuses})
    assert res["outcome"] == "escalated"
    assert ft.in_cooldown("gateway")
    assert orch.starts == []


# --------------------------------------------------------------- check_and_heal loop
@pytest.fixture
def wd(monkeypatch):
    # import here so a missing docker socket doesn't matter until now, and
    # neutralise the real sleep inside runbooks
    import watchdog as W
    monkeypatch.setattr(R.time, "sleep", lambda *_: None)
    return W


def test_heal_single_down_service(wd):
    orch = FakeOrchestrator({"tp-s0": "running", "wdb-s0": "running",
                             "rdb-s0": "exited", "idb-s0": "running"})
    ft = _ft()
    wd.check_and_heal(orch, ft, services=["tp-s0", "wdb-s0", "rdb-s0", "idb-s0"],
                      recovery_delay_sec=0)
    assert orch.starts == ["rdb-s0"]             # only the down one
    assert orch.status("rdb-s0") == "running"


def test_dependency_order_heals_tp_first_then_dependent(wd):
    # both tp and its rdb are down: tp is visited first, recovers, and the rdb
    # then reclassifies to a normal restart within the same pass
    orch = FakeOrchestrator({"tp-s0": "exited", "wdb-s0": "running",
                             "rdb-s0": "exited", "idb-s0": "running"})
    ft = _ft()
    wd.check_and_heal(orch, ft, services=["tp-s0", "wdb-s0", "rdb-s0", "idb-s0"],
                      recovery_delay_sec=0)
    assert orch.starts == ["tp-s0", "rdb-s0"]    # tp before dependent, both restarted
    assert orch.status("rdb-s0") == "running"


def test_dependent_deferred_when_tp_stays_down(wd):
    orch = FakeOrchestrator({"tp-s0": "exited", "rdb-s0": "exited"})
    orch.start_result["tp-s0"] = "exited"        # tp restart fails
    ft = _ft()
    wd.check_and_heal(orch, ft, services=["tp-s0", "rdb-s0"], recovery_delay_sec=0)
    # tp was attempted (and failed); rdb was deferred, never restarted
    assert "tp-s0" in orch.starts
    assert "rdb-s0" not in orch.starts


def test_repeated_crashes_trip_flapping_then_cooldown(wd):
    orch = FakeOrchestrator({"gateway": "exited"})
    ft = flap_mod.FlapTracker(window_sec=1000, threshold=3, cooldown_sec=500, clock=FakeClock())
    services = ["gateway"]
    # 3 passes: each time it "recovers" on restart but has crashed again by the
    # next pass -> 3 restarts recorded, no escalation yet
    for _ in range(3):
        orch.statuses["gateway"] = "exited"
        wd.check_and_heal(orch, ft, services=services, recovery_delay_sec=0)
    assert ft.restart_count("gateway") == 3
    restarts_before = orch.starts.count("gateway")
    # 4th pass: now flapping -> escalate, NOT another restart
    orch.statuses["gateway"] = "exited"
    wd.check_and_heal(orch, ft, services=services, recovery_delay_sec=0)
    assert orch.starts.count("gateway") == restarts_before   # no new restart
    assert ft.in_cooldown("gateway")
    # subsequent pass while cooling down: still left alone
    orch.statuses["gateway"] = "exited"
    wd.check_and_heal(orch, ft, services=services, recovery_delay_sec=0)
    assert orch.starts.count("gateway") == restarts_before


def test_health_probe_catches_running_but_wedged(wd):
    orch = FakeOrchestrator({"rdb-s0": "running", "tp-s0": "running"})
    ft = _ft()
    # probe says rdb-s0 is wedged even though its container is 'running'
    probe = lambda s: False if s == "rdb-s0" else True
    wd.check_and_heal(orch, ft, health_probe=probe,
                      services=["tp-s0", "rdb-s0"], recovery_delay_sec=0)
    assert orch.starts == ["rdb-s0"]             # restarted despite being 'running'
