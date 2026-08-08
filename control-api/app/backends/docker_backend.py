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

    def set_env(self, service: str, env_overrides: dict) -> bool:
        """Apply env var overrides to a service that need to take effect
        immediately (e.g. a connector's symbol-group scope) - docker has no
        "update running container's env" call, so this recreates the
        container in place: same image/name/volumes/network/restart-policy,
        merged env. Used sparingly, only where a live config change genuinely
        needs a fresh process (a feed sim reads its symbol universe once at
        startup)."""
        c = self._find(service)
        if c is None or self.client is None:
            log.warning("set_env requested for unknown service %s", service)
            return False
        try:
            c.reload()
            attrs = c.attrs
            cfg = attrs["Config"]
            host_cfg = attrs["HostConfig"]
            name = c.name
            image = cfg.get("Image")

            env_map = {}
            for e in cfg.get("Env") or []:
                if "=" in e:
                    k, v = e.split("=", 1)
                    env_map[k] = v
            env_map.update(env_overrides)

            networks = (attrs.get("NetworkSettings", {}) or {}).get("Networks", {}) or {}
            network_names = list(networks.keys())

            c.stop(timeout=5)
            c.remove()

            new_c = self.client.containers.run(
                image,
                command=cfg.get("Cmd"),
                name=name,
                environment=env_map,
                volumes=host_cfg.get("Binds") or None,
                restart_policy=host_cfg.get("RestartPolicy") or None,
                network=network_names[0] if network_names else None,
                labels=cfg.get("Labels") or None,
                detach=True,
            )
            for net_name in network_names[1:]:
                try:
                    self.client.networks.get(net_name).connect(new_c)
                except DockerException as exc:
                    log.warning("could not reattach %s to network %s: %s", service, net_name, exc)
            return True
        except DockerException as exc:
            log.warning("set_env for %s failed: %s", service, exc)
            return False

    def logs(self, service: str, tail: int = 200) -> Optional[str]:
        c = self._find(service)
        if c is None:
            return None
        return c.logs(tail=tail).decode(errors="replace")
