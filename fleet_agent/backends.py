"""
backends.py - the real cluster-specific executors behind DataPlaneBackend.

These shell out to `helm` (for the k8s clouds: AWS/EKS, Azure/AKS, GCP/GKE) or
to `docker compose` + the repo's own gen_topology.py (for on-prem / single-box).
Both realize the SAME single knob - shardCount - that the whole product is
built around, so the agent isn't a parallel deployment path; it drives the
exact mechanism the Helm chart and docker-compose already expose.

None of this runs in CI (there's no cluster in the sandbox). The reconcile
DECISIONS are tested in provisioner.py with a fake backend; this file is the
thin, real edge that only works in a live environment.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Optional

from .provisioner import ReconcileResult

log = logging.getLogger("fleet_agent.backends")


def _run(cmd: list, cwd: Optional[str] = None, timeout: int = 900) -> tuple[int, str]:
    log.info("exec: %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


class HelmBackend:
    """Reconciles an in-cluster release with `helm upgrade --set shardCount=N`.

    Works the same on EKS, AKS, and GKE - the agent runs with a service
    account bound to the namespace, so no cloud SDK is needed here; kubectl's
    context is the ambient cluster the agent lives in.
    """

    def __init__(self, release: str, chart_path: str, namespace: str = "default",
                 extra_set: Optional[dict] = None):
        self.release = release
        self.chart_path = chart_path
        self.namespace = namespace
        self.extra_set = extra_set or {}

    def current_shard_count(self) -> Optional[int]:
        rc, out = _run(["helm", "get", "values", self.release,
                        "-n", self.namespace, "-o", "json"])
        if rc != 0:
            return None  # release not installed yet
        try:
            values = json.loads(out) if out else {}
        except json.JSONDecodeError:
            return None
        v = values.get("shardCount")
        return int(v) if isinstance(v, (int, str)) and str(v).isdigit() else None

    def reconcile(self, shard_count: int) -> ReconcileResult:
        args = ["helm", "upgrade", "--install", self.release, self.chart_path,
                "-n", self.namespace, "--create-namespace",
                "--set", f"shardCount={shard_count}", "--wait", "--timeout", "10m"]
        for k, v in self.extra_set.items():
            args += ["--set", f"{k}={v}"]
        rc, out = _run(args)
        steps = [f"helm upgrade --install {self.release} --set shardCount={shard_count}"]
        if rc != 0:
            return ReconcileResult(ok=False, shard_count=shard_count,
                                   detail=f"helm upgrade failed: {out[-500:]}", steps=steps)
        return ReconcileResult(ok=True, shard_count=shard_count,
                               detail="helm release reconciled and rollout complete",
                               steps=steps)

    def teardown(self) -> ReconcileResult:
        rc, out = _run(["helm", "uninstall", self.release, "-n", self.namespace])
        if rc != 0:
            return ReconcileResult(ok=False, detail=f"helm uninstall failed: {out[-500:]}")
        return ReconcileResult(ok=True, detail="helm release uninstalled")

    def reconcile_spec(self, desired: dict) -> ReconcileResult:
        """Provision a full TickHouse spec: render per-component hardware +
        shard ranges into helm values and upgrade."""
        from .tickhouse_render import render_helm_sets, summarize
        sets = render_helm_sets(desired)
        args = ["helm", "upgrade", "--install", self.release, self.chart_path,
                "-n", self.namespace, "--create-namespace"]
        for s in sets:
            args += ["--set", s]
        args += ["--wait", "--timeout", "15m"]
        rc, out = _run(args)
        steps = [f"helm upgrade with {len(sets)} settings ({summarize(desired)})"]
        n = desired.get("shardCount")
        if rc != 0:
            return ReconcileResult(ok=False, shard_count=n,
                                   detail=f"helm upgrade failed: {out[-500:]}", steps=steps)
        return ReconcileResult(ok=True, shard_count=n,
                               detail="tickhouse reconciled via helm", steps=steps)


class ComposeBackend:
    """Reconciles a single-box / on-prem stack: regenerate docker-compose.yml +
    shards.json for N shards via the repo's gen_topology.py, then bring it up.
    """

    def __init__(self, project_dir: str, python: str = "python3"):
        self.project_dir = project_dir
        self.python = python

    def _shards_json_path(self) -> str:
        return os.path.join(self.project_dir, "data-plane", "shards.json")

    def current_shard_count(self) -> Optional[int]:
        path = self._shards_json_path()
        if not os.path.exists(path):
            return None
        try:
            with open(path) as fh:
                return int(json.load(fh).get("shardCount"))
        except Exception:  # noqa: BLE001
            return None

    def reconcile(self, shard_count: int) -> ReconcileResult:
        steps = []
        rc, out = _run([self.python, "scripts/gen_topology.py",
                        "--shards", str(shard_count),
                        "--compose", "docker-compose.yml",
                        "--shards-json", "data-plane/shards.json"],
                       cwd=self.project_dir)
        steps.append(f"gen_topology --shards {shard_count}")
        if rc != 0:
            return ReconcileResult(ok=False, shard_count=shard_count,
                                   detail=f"topology generation failed: {out[-500:]}", steps=steps)
        rc, out = _run(["docker", "compose", "up", "-d", "--remove-orphans"],
                       cwd=self.project_dir)
        steps.append("docker compose up -d --remove-orphans")
        if rc != 0:
            return ReconcileResult(ok=False, shard_count=shard_count,
                                   detail=f"compose up failed: {out[-500:]}", steps=steps)
        return ReconcileResult(ok=True, shard_count=shard_count,
                               detail="compose stack reconciled", steps=steps)

    def teardown(self) -> ReconcileResult:
        rc, out = _run(["docker", "compose", "down"], cwd=self.project_dir)
        if rc != 0:
            return ReconcileResult(ok=False, detail=f"compose down failed: {out[-500:]}")
        return ReconcileResult(ok=True, detail="compose stack brought down")

    def reconcile_spec(self, desired: dict) -> ReconcileResult:
        """Provision a full TickHouse spec on-prem: regenerate topology for the
        shard count and bring the stack up with the profile/OS env applied."""
        from .tickhouse_render import render_compose_env, summarize
        n = desired.get("shardCount")
        eod = desired.get("eod_config") or {}
        steps = []
        rc, out = _run([self.python, "scripts/gen_topology.py", "--shards", str(n),
                        "--compose", "docker-compose.yml", "--shards-json", "data-plane/shards.json",
                        "--eod-hour", str(eod.get("eod_hour_utc", 0)),
                        "--idb-retention-days", str(eod.get("idb_retention_days", 5)),
                        "--rdb-retention-min", str(eod.get("rdb_retention_min", 2)),
                        "--hdb-retention-days", str(eod.get("hdb_retention_days", 0))],
                       cwd=self.project_dir)
        steps.append(f"gen_topology --shards {n}")
        if rc != 0:
            return ReconcileResult(ok=False, shard_count=n,
                                   detail=f"topology generation failed: {out[-500:]}", steps=steps)
        env = {**os.environ, **render_compose_env(desired)}
        proc = subprocess.run(["docker", "compose", "up", "-d", "--remove-orphans"],
                              cwd=self.project_dir, env=env, capture_output=True, text=True, timeout=900)
        steps.append(f"docker compose up -d ({summarize(desired)})")
        if proc.returncode != 0:
            return ReconcileResult(ok=False, shard_count=n,
                                   detail=f"compose up failed: {(proc.stderr or proc.stdout)[-500:]}",
                                   steps=steps)
        return ReconcileResult(ok=True, shard_count=n,
                               detail="tickhouse reconciled via compose", steps=steps)


def build_backend(cfg) -> object:
    """Pick a backend from config. `helm` for the cloud (k8s) environments,
    `compose` for on-prem / single-box."""
    if cfg.backend == "compose":
        return ComposeBackend(cfg.project_dir, python=cfg.python)
    return HelmBackend(cfg.helm_release, cfg.helm_chart, namespace=cfg.helm_namespace)
