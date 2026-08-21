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

    def reconcile(self, n, gateway_replicas=None):
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
        def reconcile(self, n, gateway_replicas=None): return ReconcileResult(ok=True, shard_count=n, detail="")
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


def test_render_helm_sets_covers_retention_policy():
    payload = _spec_payload()["desired"]
    payload["eod_config"] = {"eod_hour_utc": 22, "idb_retention_days": 3,
                             "rdb_retention_min": 15, "hdb_retention_days": 30}
    joined = "\n".join(tr.render_helm_sets(payload))
    assert "eod.hourUtc=22" in joined
    assert "idb.retentionDays=3" in joined
    assert "rdb.retentionMin=15" in joined
    assert "hdb.retentionDays=30" in joined


def test_render_helm_sets_omits_retention_keys_when_not_configured():
    # no eod_config at all in the payload - must not KeyError, must not emit
    # rdb./hdb. sets that would override the chart's own defaults for no reason
    joined = "\n".join(tr.render_helm_sets(_spec_payload()["desired"]))
    assert "rdb.retentionMin" not in joined
    assert "hdb.retentionDays" not in joined


def test_render_compose_env():
    env = tr.render_compose_env(_spec_payload()["desired"])
    assert env["SHARD_COUNT"] == "2"
    assert env["TH_SHARD_RANGES"] == "A-M;N-Z"
    assert env["TH_PROFILE"] == "low-latency"


def test_render_helm_sets_cpu_pinning_tuning_sets_guaranteed_qos():
    # "cpu-pinning" in a component's tuning (the low-latency profile's
    # default - see tickhouse.py's _PROFILE_TUNING) must set limits.cpu equal
    # to requests.cpu, not just requests.cpu alone - that's what makes the
    # pod Guaranteed-QoS-eligible for the kubelet's static CPUManager. A
    # component WITHOUT "cpu-pinning" in its tuning must NOT get a limits.cpu
    # override (that would silently change resourcing for profiles that
    # never asked for pinning).
    payload = _spec_payload()["desired"]
    payload["components"][0]["hardware"]["tuning"] = ["cpu-pinning", "core-isolation"]
    joined = "\n".join(tr.render_helm_sets(payload))
    assert "resources.tickerplant.limits.cpu=8" in joined
    assert "resources.gateway.limits.cpu" not in joined  # gateway's hardware has no tuning set


def test_render_helm_sets_numa_node_sets_node_selector():
    payload = _spec_payload()["desired"]
    payload["components"][0]["hardware"]["numa_node"] = "0"
    joined = "\n".join(tr.render_helm_sets(payload))
    assert "nodeSelectors.tickerplant.numa-node=0" in joined


def test_render_helm_sets_no_pinning_fields_emits_neither():
    # the common case (no NUMA fields set at all) must change nothing -
    # HardwareSpec.cpuset/numa_node default to "" precisely so this stays
    # a no-op until an operator explicitly opts in.
    joined = "\n".join(tr.render_helm_sets(_spec_payload()["desired"]))
    assert "limits.cpu" not in joined
    assert "nodeSelectors" not in joined


def test_render_compose_env_cpuset_passthrough_for_supported_components():
    payload = _spec_payload()["desired"]
    payload["components"][0]["hardware"]["cpuset"] = "0-3"
    payload["components"][0]["hardware"]["numa_node"] = "0"
    env = tr.render_compose_env(payload)
    assert env["TP_CPUSET"] == "0-3"
    assert env["TP_NUMA_NODE"] == "0"


def test_render_compose_env_omits_pinning_when_unset():
    env = tr.render_compose_env(_spec_payload()["desired"])
    assert "TP_CPUSET" not in env
    assert "TP_NUMA_NODE" not in env


# ---- KX installer plan ----------------------------------------------------

def test_kx_installer_plan_is_ordered_and_covers_licence_and_verify():
    inst = KxInstaller(KxInstallConfig(
        source="url", binary_url="https://artifacts.example.com/kx/q-linux.tgz",
        license_path="/run/secrets/kc.lic", install_dir="/opt/kx", qhome="/opt/kx/q"))
    labels = [label for label, _ in inst.plan()]
    assert labels[0] == "make install dir"
    assert "download KX binary" in labels
    assert "install licence" in labels
    assert labels[-1] == "verify"
    # the download step actually references the configured URL
    download = next(argv for label, argv in inst.plan() if label == "download KX binary")
    assert "https://artifacts.example.com/kx/q-linux.tgz" in download


def test_kx_installer_preflight_flags_missing_config_and_non_linux():
    problems = KxInstaller(KxInstallConfig(source="url", binary_url="", license_path="",
                                           os_type="windows")).preflight()
    assert any("linux" in p for p in problems)
    assert any("binary_url" in p for p in problems)
    assert any("license_path" in p for p in problems)
