import logging
import os

log = logging.getLogger("watchdog.orchestrator")

_backend = os.environ.get("ORCHESTRATOR_BACKEND", "docker").lower()

if _backend == "kubernetes":
    from backends.kubernetes_backend import KubernetesOrchestrator
    orchestrator = KubernetesOrchestrator()
    log.info("watchdog orchestrator backend: kubernetes (namespace=%s)", orchestrator.namespace)
elif _backend == "docker":
    from backends.docker_backend import Orchestrator
    orchestrator = Orchestrator()
    log.info("watchdog orchestrator backend: docker")
else:
    raise ValueError(f"unknown ORCHESTRATOR_BACKEND: {_backend!r} (expected 'docker' or 'kubernetes')")
