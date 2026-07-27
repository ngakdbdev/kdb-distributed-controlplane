"""
kubernetes_backend.py - drives the data-plane workloads via the Kubernetes
API instead of the Docker socket. This is what the Helm-deployed control API
uses in any cluster (EKS/AKS/GKE/k3s/on-prem) - it needs no cloud-specific
code at all, because it only ever talks to the Kubernetes API server, which
looks identical everywhere. That portability is the entire point of routing
the SaaS through Kubernetes rather than writing separate AWS/Azure/GCP
orchestration code.

Auth: uses in-cluster config (the pod's own ServiceAccount token) when
running inside a cluster, falling back to the local kubeconfig for
out-of-cluster development/testing. RBAC for the ServiceAccount is scoped to
exactly "read/patch Deployments and read Pod logs in this one namespace" -
see helm/templates/rbac.yaml.

Service model: every data-plane process is a single-replica Deployment named
after its service (e.g. "rdb-a-m"). start/stop map to scaling replicas
1/0. restart triggers a rollout by patching a timestamp annotation on the
pod template, which is the standard way to force Kubernetes to recreate
pods without changing the image.
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException

from ..config import settings

log = logging.getLogger("orchestrator.kubernetes")


class KubernetesOrchestrator:
    def __init__(self, namespace: Optional[str] = None):
        self.namespace = namespace or os.environ.get("KUBE_NAMESPACE", "default")
        self.available = True
        try:
            config.load_incluster_config()
            log.info("using in-cluster Kubernetes config")
        except ConfigException:
            try:
                config.load_kube_config()
                log.info("using local kubeconfig (out-of-cluster dev mode)")
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
        updated = dep.status.updated_replicas or 0
        if desired == 0:
            return "exited"
        if available >= desired and updated >= desired:
            return "running"
        return "restarting"

    def status_all(self) -> dict:
        return {svc: self.status(svc) for svc in settings.managed_services}

    def _scale(self, service: str, replicas: int) -> bool:
        try:
            self.apps.patch_namespaced_deployment_scale(
                service, self.namespace, {"spec": {"replicas": replicas}}
            )
            return True
        except ApiException as exc:
            log.warning("scale %s to %d failed: %s", service, replicas, exc)
            return False

    def start(self, service: str) -> bool:
        if self._get_deployment(service) is None:
            log.warning("start requested for unknown service %s", service)
            return False
        return self._scale(service, 1)

    def stop(self, service: str) -> bool:
        if self._get_deployment(service) is None:
            return False
        return self._scale(service, 0)

    def restart(self, service: str) -> bool:
        dep = self._get_deployment(service)
        if dep is None:
            log.warning("restart requested for unknown service %s", service)
            return False
        now = datetime.now(timezone.utc).isoformat()
        body = {"spec": {"template": {"metadata": {"annotations": {
            "kdb-control-plane/restartedAt": now
        }}}}}
        try:
            self.apps.patch_namespaced_deployment(service, self.namespace, body)
        except ApiException as exc:
            log.warning("restart (rollout) of %s failed: %s", service, exc)
            return False
        # if it had been scaled to 0, a restart should also bring it back up
        if (dep.spec.replicas or 0) == 0:
            self._scale(service, 1)
        return True

    def logs(self, service: str, tail: int = 200) -> Optional[str]:
        if not self.available:
            return None
        try:
            pods = self.core.list_namespaced_pod(
                self.namespace, label_selector=f"app={service}"
            )
        except ApiException as exc:
            log.warning("could not list pods for %s: %s", service, exc)
            return None
        if not pods.items:
            return None
        pod_name = pods.items[0].metadata.name
        try:
            return self.core.read_namespaced_pod_log(
                pod_name, self.namespace, tail_lines=tail
            )
        except ApiException as exc:
            return f"(could not fetch logs: {exc})"
