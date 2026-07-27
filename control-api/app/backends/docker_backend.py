"""
docker_orchestrator.py - thin wrapper around the Docker SDK so the control
API and watchdog can start/stop/restart data-plane containers by their
docker-compose service name, without shelling out to `docker compose`.

Requires the control-api container to have the host's Docker socket mounted
(see docker-compose.yml: `/var/run/docker.sock:/var/run/docker.sock`).
"""
import logging
from typing import Optional

import docker
from docker.errors import DockerException, NotFound

from ..config import settings

log = logging.getLogger("orchestrator")


class Orchestrator:
    def __init__(self):
        try:
            self.client = docker.from_env()
        except DockerException as exc:
            log.error("could not connect to docker socket: %s", exc)
            self.client = None

    def _container_name(self, service: str) -> str:
        return f"{settings.docker_compose_project}-{service}-1"

    def _find(self, service: str):
        if self.client is None:
            return None
        candidates = [self._container_name(service), service]
        for name in candidates:
            try:
                return self.client.containers.get(name)
            except NotFound:
                continue
        # fall back to a label/filter search in case naming differs
        matches = self.client.containers.list(
            all=True, filters={"label": f"com.docker.compose.service={service}"}
        )
        return matches[0] if matches else None

    def status(self, service: str) -> str:
        c = self._find(service)
        if c is None:
            return "not_found"
        c.reload()
        return c.status  # "running" / "exited" / "restarting" / ...

    def status_all(self) -> dict:
        return {svc: self.status(svc) for svc in settings.managed_services}

    def start(self, service: str) -> bool:
        c = self._find(service)
        if c is None:
            log.warning("start requested for unknown service %s", service)
            return False
        c.start()
        return True

    def stop(self, service: str) -> bool:
        c = self._find(service)
        if c is None:
            return False
        c.stop(timeout=5)
        return True

    def restart(self, service: str) -> bool:
        c = self._find(service)
        if c is None:
            log.warning("restart requested for unknown service %s", service)
            return False
        c.restart(timeout=5)
        return True

    def logs(self, service: str, tail: int = 200) -> Optional[str]:
        c = self._find(service)
        if c is None:
            return None
        return c.logs(tail=tail).decode(errors="replace")
