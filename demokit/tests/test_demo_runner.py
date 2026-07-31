"""
Unit tests for demokit.demo_runner - drives the whole five-act flow against a
fake control-api. Proves the runner detects the watchdog heal and reads the
audit trail, with no containers and no KDB-X. The FakeApi scripts a realistic
sequence: the killed process reports DOWN for a couple of polls, then the
"watchdog" brings it back and an auto_heal audit row appears.
"""
from demokit.demo_runner import DemoRunner, Narrator


class FakeApi:
    def __init__(self, down_polls=2, heal=True):
        self.tps = ["tp-s0", "tp-s1"]
        self.services = {
            "tp-s0": "running", "tp-s1": "running",
            "rdb-s0": "running", "rdb-s1": "running",
            "gateway": "running",
        }
        self.down_polls = down_polls
        self.heal = heal
        self._stopped = None
        self._polls_since_stop = 0
        self.connectors = [
            {"id": 1, "name": "bpipe-sim", "enabled": False},
            {"id": 2, "name": "crims-sim", "enabled": False},
            {"id": 3, "name": "some-other", "enabled": True},
        ]
        self._metric_calls = 0
        self.logged_in = False
        self.login_email = None
        self.toggled = []

    def health(self):
        return {"status": "up"}

    def login(self, email, password):
        self.logged_in = True
        self.login_email = email

    def _recovered(self):
        return self.heal and self._stopped is not None and self._polls_since_stop > self.down_polls

    def topology_status(self):
        st = dict(self.services)
        if self._stopped is not None:
            self._polls_since_stop += 1
            st[self._stopped] = "running" if self._recovered() else "exited"
        return st

    def stop_service(self, service):
        self._stopped = service
        self._polls_since_stop = 0
        return {"service": service, "stopped": True}

    def list_connectors(self):
        return [dict(c) for c in self.connectors]

    def toggle_connector(self, connector_id):
        for c in self.connectors:
            if c["id"] == connector_id:
                c["enabled"] = not c["enabled"]
                self.toggled.append(c["name"])
        return {}

    def metrics_snapshot(self):
        self._metric_calls += 1
        return {"rowCounts": {"trade": 100 * self._metric_calls,
                              "risk": 10 * self._metric_calls}}

    def audit(self, limit=20, action=None):
        if self._recovered() and action in (None, "auto_heal"):
            return [{"actor": "watchdog", "action": "auto_heal",
                     "target": self._stopped, "outcome": "success"}]
        return []


def _silent_runner(api, **kw):
    narrator = Narrator(colour=False, pause_s=0.0, out=lambda *_: None,
                        sleep=lambda *_: None)
    t = [0.0]
    clock = lambda: t[0]
    sleep = lambda s: t.__setitem__(0, t[0] + s)
    return DemoRunner(api, narrator, poll_s=1.0, heal_timeout_s=60.0,
                      clock=clock, sleep=sleep, **kw)


def test_full_demo_happy_path():
    api = FakeApi(down_polls=2, heal=True)
    runner = _silent_runner(api)
    result = runner.run("admin@demo-bank.local", "pw")

    assert result.logged_in
    assert api.login_email == "admin@demo-bank.local"
    assert result.shards_seen == 2                    # tp-s0, tp-s1
    assert result.feeds_enabled == ["bpipe-sim", "crims-sim"]
    assert "some-other" not in api.toggled            # already-enabled feed untouched
    assert result.ingest_climbed is True              # metrics increase each poll
    assert result.heal is not None
    assert result.heal.went_down is True
    assert result.heal.recovered is True
    assert result.heal.heal_event["target"] == "tp-s0"
    assert result.heal.heal_event["action"] == "auto_heal"
    assert result.ok is True


def test_chaos_targets_configured_service():
    api = FakeApi()
    runner = _silent_runner(api, chaos_service="tp-s1")
    result = runner.run("admin@demo-bank.local", "pw")
    assert api._stopped == "tp-s1"
    assert result.heal.service == "tp-s1"
    assert result.heal.heal_event["target"] == "tp-s1"


def test_demo_reports_failure_when_watchdog_never_heals():
    api = FakeApi(heal=False)
    runner = _silent_runner(api)
    result = runner.run("admin@demo-bank.local", "pw")
    assert result.heal.went_down is True
    assert result.heal.recovered is False
    assert result.heal.heal_event is None
    assert result.ok is False


def test_feeds_not_re_enabled_when_already_on():
    api = FakeApi()
    for c in api.connectors:
        c["enabled"] = True
    runner = _silent_runner(api)
    result = runner.run("admin@demo-bank.local", "pw", do_chaos=False)
    assert result.feeds_enabled == []
    assert api.toggled == []
