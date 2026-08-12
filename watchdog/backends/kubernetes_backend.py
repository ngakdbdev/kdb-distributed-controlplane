import logging
import os
from datetime import datetime, timezone
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException

log = logging.getLogger("watchdog.orchestrator.kubernetes")


class KubernetesOrchestrator:
    def __init__(self, namespace: Optional[str] = None):
        self.namespace = namespace or os.environ.get("KUBE_NAMESPACE", "default")
        self.available = True
        try:
            config.load_incluster_config()
        except ConfigException:
            try:
                config.load_kube_config()
            except ConfigException as exc:
                log.error("no usable Kubernetes config found: %s", exc)
                self.available = False
        self.apps = client.AppsV1Api() if self.available else None
        self.core = client.CoreV1Api() if self.available else None

    def _get_deployment(self, service: str):
        if not self.available:
            return None
        try:
            return self.apps.read_namespaced_deployment(service, self.namespace)
        except ApiException as exc:
            if exc.status == 404:
                return None
            log.warning("error reading deployment %s: %s", service, exc)
            return None

    def status(self, service: str) -> str:
        dep = self._get_deployment(service)
        if dep is None:
            return "not_found"
        desired = dep.spec.replicas or 0
        available = dep.status.available_replicas or 0
        if desired == 0:
            return "exited"
        return "running" if available >= desired else "restarting"

    def oom_killed(self, service: str) -> bool:
        """Whether any pod backing this deployment most recently terminated
        with reason OOMKilled. False on any lookup failure - this only gates
        a longer cooldown, never a heal action, so failing closed just falls
        back to the plain flapping runbook, never blocks a real restart."""
        dep = self._get_deployment(service)
        if dep is None or not self.available:
            return False
        try:
            selector = dep.spec.selector.match_labels or {}
            if not selector:
                return False
            label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
            pods = self.core.list_namespaced_pod(self.namespace, label_selector=label_selector)
            for pod in pods.items:
                for cs in (pod.status.container_statuses or []):
                    term = cs.last_state.terminated if cs.last_state else None
                    if term is not None and term.reason == "OOMKilled":
                        return True
        except ApiException as exc:
            log.warning("error checking OOM status for %s: %s", service, exc)
        return False

    def start(self, service: str) -> bool:
        dep = self._get_deployment(service)
        if dep is None:
            return False
        try:
            if (dep.spec.replicas or 0) == 0:
                self.apps.patch_namespaced_deployment_scale(
                    service, self.namespace, {"spec": {"replicas": 1}}
                )
            # a crashed-but-still-"desired:1" pod needs a rollout nudge, not a
            # scale operation, to actually get a fresh pod
            now = datetime.now(timezone.utc).isoformat()
            body = {"spec": {"template": {"metadata": {"annotations": {
                "kdb-control-plane/restartedAt": now
            }}}}}
            self.apps.patch_namespaced_deployment(service, self.namespace, body)
            return True
        except ApiException as exc:
            log.warning("start/restart of %s failed: %s", service, exc)
            return False
