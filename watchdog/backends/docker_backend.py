import logging
import os

import docker
from docker.errors import DockerException, NotFound

log = logging.getLogger("watchdog.orchestrator")

COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "kdb-control-plane")


class Orchestrator:
    def __init__(self):
        try:
            self.client = docker.from_env()
        except DockerException as exc:
            log.error("could not connect to docker socket: %s", exc)
            self.client = None

    def _find(self, service: str):
        if self.client is None:
            return None
        for name in (f"{COMPOSE_PROJECT}-{service}-1", service):
            try:
                return self.client.containers.get(name)
            except NotFound:
                continue
        matches = self.client.containers.list(
            all=True, filters={"label": f"com.docker.compose.service={service}"}
        )
        return matches[0] if matches else None

    def status(self, service: str) -> str:
        c = self._find(service)
        if c is None:
            return "not_found"
        c.reload()
        return c.status

    def oom_killed(self, service: str) -> bool:
        """Whether this container's most recent exit was an OOM kill - the
        docker daemon tracks this directly (State.OOMKilled), no guessing
        from exit codes needed. False (not True) on any lookup failure -
        this only gates a longer cooldown, never a heal action, so failing
        closed here just means falling back to the plain flapping runbook,
        never blocking a real restart attempt."""
        c = self._find(service)
        if c is None:
            return False
        try:
            c.reload()
            return bool(c.attrs.get("State", {}).get("OOMKilled", False))
        except DockerException:
            return False

    def start(self, service: str) -> bool:
        c = self._find(service)
        if c is None:
            return False
        c.start()
        return True
