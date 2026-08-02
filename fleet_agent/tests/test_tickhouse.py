"""
Tests for the agent's TickHouse provisioning path: full-spec provision routes to
the backend's reconcile_spec, the helm/compose renderer, and the KX installer plan.
"""
from fleet_agent.provisioner import Provisioner, ReconcileResult
from fleet_agent import tickhouse_render as tr
from fleet_agent.kx_installer import KxInstaller, KxInstallConfig


def _spec_payload(shards=2):
    return {"desired": {
        "tickhouse": "acme-emea", "shardCount": shards, "profile": "low-latency", "os": "ubuntu-22.04",
        "shards": [{"id": "sam", "label": "A-M", "lo": "A", "hi": "M"},
                   {"id": "snz", "label": "N-Z", "lo": "N", "hi": "Z"}][:shards],
        "gateway_config": {"port": 5050},
        "components": [
            {"type": "tickerplant", "per_shard": True,
             "hardware": {"vcpus": 8, "memory_gb": 32, "disk_gb": 200, "disk_tier": "nvme",
                          "instance_type": "c7i.2xlarge", "nic": "kernel-bypass"}},
            {"type": "gateway", "per_shard": False,
             "hardware": {"vcpus": 8, "memory_gb": 16, "disk_gb": 100, "disk_tier": "ssd",
                          "instance_type": "c7i.2xlarge", "nic": "standard"}},
        ],
    }}


class SpecBackend:
    def __init__(self):
        self.spec_seen = None

    def current_shard_count(self):
        return None

    def reconcile(self, n):
        return ReconcileResult(ok=True, shard_count=n, detail="count path")

    def reconcile_spec(self, desired):
        self.spec_seen = desired
        return ReconcileResult(ok=True, shard_count=desired["shardCount"], detail="spec path")

    def teardown(self):
        return ReconcileResult(ok=True, detail="down")


# ---- provisioner routes full spec to reconcile_spec ----------------------

def test_full_spec_routes_to_reconcile_spec():
    backend = SpecBackend()
    res = Provisioner(backend).provision(_spec_payload())
    assert res.ok and res.detail == "spec path"
    assert backend.spec_seen["tickhouse"] == "acme-emea"


def test_simple_shardcount_still_uses_count_path():
    backend = SpecBackend()
    res = Provisioner(backend).provision({"desired": {"shardCount": 3}})
    assert res.ok and res.detail == "count path"


def test_spec_provision_needs_backend_support():
    class NoSpec:
        def current_shard_count(self): return None
        def reconcile(self, n): return ReconcileResult(ok=True, shard_count=n, detail="")
        def teardown(self): return ReconcileResult(ok=True, detail="")
    res = Provisioner(NoSpec()).provision(_spec_payload())
    assert not res.ok and "does not support" in res.detail


# ---- helm / compose renderer ---------------------------------------------

def test_render_helm_sets_covers_shards_and_hardware():
    sets = tr.render_helm_sets(_spec_payload()["desired"])
    joined = "\n".join(sets)
    assert "shardCount=2" in sets
    assert "shardRanges=A-M;N-Z" in sets
    assert "resources.tickerplant.requests.cpu=8" in joined
    assert "resources.tickerplant.requests.memory=32Gi" in joined
    assert "nodePools.tickerplant.instanceType=c7i.2xlarge" in joined
    assert "gateway.port=5050" in joined


def test_render_compose_env():
    env = tr.render_compose_env(_spec_payload()["desired"])
    assert env["SHARD_COUNT"] == "2"
    assert env["TH_SHARD_RANGES"] == "A-M;N-Z"
    assert env["TH_PROFILE"] == "low-latency"


# ---- KX installer plan ----------------------------------------------------

def test_kx_installer_plan_is_ordered_and_covers_licence_and_verify():
    inst = KxInstaller(KxInstallConfig(
        binary_url="https://artifacts.example.com/kx/q-linux.tgz",
        license_path="/run/secrets/k4.lic", install_dir="/opt/kx", qhome="/opt/kx/q"))
    labels = [label for label, _ in inst.plan()]
    assert labels[0] == "make install dir"
    assert "download KX binary" in labels
    assert "install licence" in labels
    assert labels[-1] == "verify"
    # the download step actually references the configured URL
    download = next(argv for label, argv in inst.plan() if label == "download KX binary")
    assert "https://artifacts.example.com/kx/q-linux.tgz" in download


def test_kx_installer_preflight_flags_missing_config_and_non_linux():
    problems = KxInstaller(KxInstallConfig(binary_url="", license_path="",
                                           os_type="windows")).preflight()
    assert any("linux" in p for p in problems)
    assert any("binary_url" in p for p in problems)
    assert any("license_path" in p for p in problems)
