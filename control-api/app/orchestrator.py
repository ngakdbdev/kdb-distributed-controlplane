"""
orchestrator.py - selects which backend drives the data-plane workloads.

  ORCHESTRATOR_BACKEND=docker      -> docker-compose, for local dev (default)
  ORCHESTRATOR_BACKEND=kubernetes  -> any K8s cluster, for the Helm chart

Every router imports `orchestrator` from here and never touches a specific
backend directly, so the rest of the app is completely unaware of which
infrastructure it's actually running on.
"""
import logging
import os

log = logging.getLogger("orchestrator")

_backend = os.environ.get("ORCHESTRATOR_BACKEND", "docker").lower()

if _backend == "kubernetes":
    from .backends.kubernetes_backend import KubernetesOrchestrator
    orchestrator = KubernetesOrchestrator()
    log.info("orchestrator backend: kubernetes (namespace=%s)", orchestrator.namespace)
elif _backend == "docker":
    from .backends.docker_backend import Orchestrator
    orchestrator = Orchestrator()
    log.info("orchestrator backend: docker")
else:
    raise ValueError(f"unknown ORCHESTRATOR_BACKEND: {_backend!r} (expected 'docker' or 'kubernetes')")
