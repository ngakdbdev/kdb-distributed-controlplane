"""
Unit tests for fleet_agent.agent - the heartbeat/dispatch loop, driven with a
fake control-plane client and a fake provisioner backend. Proves a provision
command flows heartbeat -> execute -> report, that service ops route, and that
a failure in one command still gets reported (and doesn't kill the loop).
"""
import json

from fleet_agent.agent import Agent
from fleet_agent.provisioner import Provisioner
from fleet_agent.tests.test_provisioner import FakeBackend


class FakeClient:
    """Serves queued command batches on successive heartbeats, records results."""

    def __init__(self, batches):
        self._batches = list(batches)
        self.heartbeats = 0
        self.reported = []          # (command_id, outcome, detail)
        self.last_status = None

    def heartbeat(self, service_status):
        self.last_status = service_status
        if self._batches:
            return self._batches.pop(0)
        return []

    def report_result(self, command_id, outcome, detail=""):
        self.reported.append((command_id, outcome, detail))
        return {"acknowledged": True}


def _provision_cmd(cmd_id, n):
    return {"id": cmd_id, "action": "provision", "service": "data-plane",
            "payload": json.dumps({"desired": {"shardCount": n, "topology": {}}})}


def test_provision_command_flows_end_to_end():
    backend = FakeBackend(current=2)
    client = FakeClient([[_provision_cmd(11, 5)]])
    agent = Agent(client, Provisioner(backend), heartbeat_interval=0)

    handled = agent.tick()

    assert handled == 1
    assert backend.reconciled_to == 5
    assert client.reported == [(11, "success", "reconciled to 5 shards: ok")]


def test_failed_reconcile_is_reported_as_failure():
    backend = FakeBackend(current=2, reconcile_ok=False)
    client = FakeClient([[_provision_cmd(12, 4)]])
    agent = Agent(client, Provisioner(backend), heartbeat_interval=0)

    agent.tick()

    assert len(client.reported) == 1
    cmd_id, outcome, _ = client.reported[0]
    assert (cmd_id, outcome) == (12, "failure")


def test_service_ops_route_to_handler():
    calls = []

    def service_op(action, service):
        calls.append((action, service))
        return True, f"{action}ed {service}"

    client = FakeClient([[{"id": 20, "action": "restart", "service": "tp-s0", "payload": "{}"}]])
    agent = Agent(client, Provisioner(FakeBackend()), service_op=service_op)

    agent.tick()

    assert calls == [("restart", "tp-s0")]
    assert client.reported == [(20, "success", "restarted tp-s0")]


def test_unknown_action_reported_as_failure():
    client = FakeClient([[{"id": 30, "action": "nonsense", "service": "x", "payload": "{}"}]])
    agent = Agent(client, Provisioner(FakeBackend()))
    agent.tick()
    assert client.reported[0][:2] == (30, "failure")


def test_one_bad_command_does_not_stop_the_batch():
    # first command raises inside handling (bad JSON payload), second is fine
    bad = {"id": 40, "action": "provision", "service": "data-plane", "payload": "{not json"}
    good = {"id": 41, "action": "restart", "service": "gateway", "payload": "{}"}
    client = FakeClient([[bad, good]])
    agent = Agent(client, Provisioner(FakeBackend()),
                  service_op=lambda a, s: (True, "ok"))

    agent.tick()

    reported = {cid: outcome for cid, outcome, _ in client.reported}
    assert reported[40] == "failure"    # bad payload
    assert reported[41] == "success"    # still handled


def test_run_stops_after_max_cycles():
    client = FakeClient([])             # nothing queued
    ticks = {"n": 0}
    slept = {"n": 0}

    agent = Agent(client, Provisioner(FakeBackend()), heartbeat_interval=5,
                  sleep=lambda s: slept.__setitem__("n", slept["n"] + 1))
    # wrap tick to count
    orig = agent.tick
    agent.tick = lambda: (ticks.__setitem__("n", ticks["n"] + 1), orig())[1]

    agent.run(max_cycles=3)

    assert ticks["n"] == 3
    assert slept["n"] == 2              # sleeps between cycles, not after the last


def test_status_probe_snapshot_is_sent():
    client = FakeClient([[]])
    agent = Agent(client, Provisioner(FakeBackend()),
                  status_probe=lambda: {"tp-s0": "running"})
    agent.tick()
    assert client.last_status == {"tp-s0": "running"}
