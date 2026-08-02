"""
provisioner.py - the pure reconcile core. No subprocess, no network: it takes
a desired-topology payload and a backend, and decides what to do. That is what
makes the provisioning logic unit-testable inside CI with a fake backend, even
though a real reconcile only works inside a live cluster.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

# a sane upper bound mirroring the control plane's topology.MAX_SHARDS; the
# agent doesn't need the topology module (the desired spec is handed to it),
# just a guard against a nonsense command.
MAX_SHARDS = 26


@dataclass
class ReconcileResult:
    ok: bool
    detail: str
    shard_count: Optional[int] = None
    steps: list = field(default_factory=list)   # human-readable trace for the audit detail

    def summary(self) -> str:
        if self.shard_count is not None and self.ok:
            return f"reconciled to {self.shard_count} shards: {self.detail}"
        return self.detail


class DataPlaneBackend(Protocol):
    """What a cluster-specific executor must provide. Real implementations live
    in backends.py; tests provide a fake."""

    def current_shard_count(self) -> Optional[int]:
        """The data plane's current shard count, or None if nothing is deployed."""
        ...

    def reconcile(self, shard_count: int) -> ReconcileResult:
        """Bring the data plane to `shard_count` shards (helm upgrade / compose)."""
        ...

    def teardown(self) -> ReconcileResult:
        """Remove the data plane entirely."""
        ...


class Provisioner:
    def __init__(self, backend: DataPlaneBackend):
        self.backend = backend

    def provision(self, payload: dict) -> ReconcileResult:
        """Reconcile to the shard count in a `provision` command payload.

        payload shape (from the control plane):
            {"desired": {"shardCount": N, "topology": {...}, "services": [...]},
             "note": "..."}
        """
        desired = (payload or {}).get("desired") or {}

        # Full TickHouse spec (declarative cluster with typed components + hardware)?
        # Render it via the spec-aware backend path. Falls back to the simple
        # shardCount path for the earlier provision command shape.
        if desired.get("components"):
            return self._provision_spec(desired)

        n = desired.get("shardCount")
        if not isinstance(n, int):
            return ReconcileResult(ok=False, detail="provision payload missing integer shardCount")
        if not (1 <= n <= MAX_SHARDS):
            return ReconcileResult(ok=False, detail=f"shardCount {n} out of range 1..{MAX_SHARDS}")

        try:
            current = self.backend.current_shard_count()
        except Exception as exc:  # noqa: BLE001 - a backend probe failure is a failed reconcile
            return ReconcileResult(ok=False, detail=f"could not read current state: {exc}")

        if current == n:
            return ReconcileResult(ok=True, shard_count=n,
                                   detail=f"already at {n} shards, no change",
                                   steps=["no-op"])
        try:
            result = self.backend.reconcile(n)
        except Exception as exc:  # noqa: BLE001
            return ReconcileResult(ok=False, shard_count=n, detail=f"reconcile raised: {exc}")
        # ensure the shard_count is stamped even if a backend forgot to
        if result.shard_count is None:
            result.shard_count = n
        return result

    def _provision_spec(self, desired: dict) -> ReconcileResult:
        n = desired.get("shardCount")
        if not isinstance(n, int) or not (1 <= n <= MAX_SHARDS):
            return ReconcileResult(ok=False, detail=f"tickhouse shardCount {n} out of range")
        if not hasattr(self.backend, "reconcile_spec"):
            return ReconcileResult(ok=False,
                                   detail="backend does not support full-spec provisioning")
        try:
            result = self.backend.reconcile_spec(desired)
        except Exception as exc:  # noqa: BLE001
            return ReconcileResult(ok=False, shard_count=n, detail=f"spec reconcile raised: {exc}")
        if result.shard_count is None:
            result.shard_count = n
        return result

    def deprovision(self) -> ReconcileResult:
        try:
            return self.backend.teardown()
        except Exception as exc:  # noqa: BLE001
            return ReconcileResult(ok=False, detail=f"teardown raised: {exc}")
