"""
Unit tests for fleet_agent.provisioner - the pure reconcile core, driven with a
fake backend so the decisions are pinned down without a cluster.
"""
from fleet_agent.provisioner import Provisioner, ReconcileResult


class FakeBackend:
    def __init__(self, current=None, reconcile_ok=True, raise_on=None):
        self._current = current
        self._reconcile_ok = reconcile_ok
        self._raise_on = raise_on or set()
        self.reconciled_to = None
        self.reconciled_gateway_replicas = None
        self.torn_down = False

    def current_shard_count(self):
        if "current" in self._raise_on:
            raise RuntimeError("kubectl unreachable")
        return self._current

    def reconcile(self, shard_count, gateway_replicas=None):
        if "reconcile" in self._raise_on:
            raise RuntimeError("helm exploded")
        self.reconciled_to = shard_count
        self.reconciled_gateway_replicas = gateway_replicas
        return ReconcileResult(ok=self._reconcile_ok, shard_count=shard_count,
                               detail="ok" if self._reconcile_ok else "helm upgrade failed")

    def teardown(self):
        self.torn_down = True
        return ReconcileResult(ok=True, detail="uninstalled")


def _payload(n):
    return {"desired": {"shardCount": n, "topology": {"shardCount": n}}, "note": ""}


def test_provision_reconciles_when_count_differs():
    b = FakeBackend(current=2)
    res = Provisioner(b).provision(_payload(4))
    assert res.ok
    assert res.shard_count == 4
    assert b.reconciled_to == 4


def test_provision_is_noop_when_already_at_target():
    b = FakeBackend(current=3)
    res = Provisioner(b).provision(_payload(3))
    assert res.ok
    assert b.reconciled_to is None          # never called reconcile
    assert "no change" in res.detail


def test_provision_from_nothing_deployed():
    b = FakeBackend(current=None)           # nothing there yet
    res = Provisioner(b).provision(_payload(2))
    assert res.ok
    assert b.reconciled_to == 2


def test_provision_rejects_bad_shard_count():
    for bad in (0, -1, 999):
        res = Provisioner(FakeBackend(current=1)).provision(_payload(bad))
        assert not res.ok


# ---- gatewayReplicas: a separate, optional knob from shardCount ----------

def _payload_with_gw(n, gw):
    return {"desired": {"shardCount": n, "gatewayReplicas": gw,
                        "topology": {"shardCount": n}}, "note": ""}


def test_provision_passes_gateway_replicas_through_to_backend():
    b = FakeBackend(current=2)
    res = Provisioner(b).provision(_payload_with_gw(2, 4))
    assert res.ok
    assert b.reconciled_gateway_replicas == 4


def test_provision_with_gateway_replicas_is_not_a_noop_even_at_same_shard_count():
    # shard count unchanged, but gatewayReplicas given - must still reconcile
    # (we don't track current gateway replica count, so "might be a no-op"
    # isn't a safe assumption; an idempotent extra helm upgrade is fine)
    b = FakeBackend(current=3)
    res = Provisioner(b).provision(_payload_with_gw(3, 5))
    assert res.ok
    assert b.reconciled_to == 3
    assert b.reconciled_gateway_replicas == 5


def test_provision_omitted_gateway_replicas_stays_a_noop_at_same_shard_count():
    b = FakeBackend(current=3)
    res = Provisioner(b).provision(_payload(3))
    assert res.ok
    assert b.reconciled_to is None  # never called reconcile - genuinely unchanged


def test_provision_rejects_non_positive_gateway_replicas():
    for bad in (0, -1, "three"):
        res = Provisioner(FakeBackend(current=2)).provision(_payload_with_gw(2, bad))
        assert not res.ok
        assert "gatewayReplicas" in res.detail


def test_provision_rejects_missing_shard_count():
    res = Provisioner(FakeBackend()).provision({"desired": {}})
    assert not res.ok
    assert "shardCount" in res.detail


def test_provision_surfaces_backend_probe_failure():
    b = FakeBackend(current=1, raise_on={"current"})
    res = Provisioner(b).provision(_payload(2))
    assert not res.ok
    assert "current state" in res.detail


def test_provision_surfaces_reconcile_exception():
    b = FakeBackend(current=1, raise_on={"reconcile"})
    res = Provisioner(b).provision(_payload(2))
    assert not res.ok
    assert "reconcile raised" in res.detail


def test_provision_reports_failure_result_from_backend():
    b = FakeBackend(current=1, reconcile_ok=False)
    res = Provisioner(b).provision(_payload(2))
    assert not res.ok
    assert res.shard_count == 2


def test_deprovision_tears_down():
    b = FakeBackend(current=2)
    res = Provisioner(b).deprovision()
    assert res.ok
    assert b.torn_down
