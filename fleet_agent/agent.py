"""
agent.py - the heartbeat loop and command dispatch.

Kept injectable (client, provisioner, service-op callable, status probe, clock,
sleep) so the whole loop is unit-tested with fakes in fleet_agent/tests - no
network, no cluster. `main()` wires the real pieces from config.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable, Optional

from .provisioner import Provisioner

log = logging.getLogger("fleet_agent.agent")

# a local service op: (action, service) -> (ok, detail). Real impl shells out to
# kubectl/docker; tests pass a fake. Default is a no-op that refuses, so a
# misconfigured agent fails loudly rather than silently claiming success.
ServiceOp = Callable[[str, str], "tuple[bool, str]"]
StatusProbe = Callable[[], dict]


def _refuse(action: str, service: str):
    return False, f"no service-op handler configured for {action} {service}"


class Agent:
    def __init__(self, client, provisioner: Provisioner,
                 service_op: Optional[ServiceOp] = None,
                 status_probe: Optional[StatusProbe] = None,
                 heartbeat_interval: float = 10.0,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.client = client
        self.provisioner = provisioner
        self.service_op = service_op or _refuse
        self.status_probe = status_probe or (lambda: {})
        self.heartbeat_interval = heartbeat_interval
        self._clock = clock
        self._sleep = sleep

    def handle_command(self, cmd: dict) -> tuple[str, str]:
        """Run one command, return (outcome, detail) for reporting back."""
        action = cmd.get("action")
        try:
            if action == "provision":
                payload = json.loads(cmd.get("payload") or "{}")
                res = self.provisioner.provision(payload)
                return ("success" if res.ok else "failure", res.summary())
            if action == "deprovision":
                res = self.provisioner.deprovision()
                return ("success" if res.ok else "failure", res.summary())
            if action in ("start", "stop", "restart"):
                ok, detail = self.service_op(action, cmd.get("service", ""))
                return ("success" if ok else "failure", detail)
            return ("failure", f"unknown action: {action}")
        except Exception as exc:  # noqa: BLE001 - never let one command kill the loop
            log.exception("command %s failed", cmd.get("id"))
            return ("failure", f"agent error: {exc}")

    def tick(self) -> int:
        """One heartbeat cycle: report status, pull commands, run + report each.
        Returns the number of commands handled (handy for tests)."""
        status = {}
        try:
            status = self.status_probe()
        except Exception as exc:  # noqa: BLE001
            log.warning("status probe failed: %s", exc)
        commands = self.client.heartbeat(status)
        for cmd in commands:
            outcome, detail = self.handle_command(cmd)
            log.info("command %s (%s) -> %s: %s",
                     cmd.get("id"), cmd.get("action"), outcome, detail)
            try:
                self.client.report_result(cmd["id"], outcome, detail)
            except Exception as exc:  # noqa: BLE001
                log.error("could not report result for command %s: %s", cmd.get("id"), exc)
        return len(commands)

    def run(self, max_cycles: Optional[int] = None) -> None:
        """Heartbeat forever (or `max_cycles` times, for tests)."""
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - keep the agent alive across API blips
                log.error("heartbeat cycle failed: %s", exc)
            cycles += 1
            if max_cycles is None or cycles < max_cycles:
                self._sleep(self.heartbeat_interval)


def main(argv: list[str] | None = None) -> int:
    from .config import AgentConfig
    from .control_client import ControlPlaneClient
    from .backends import build_backend

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = AgentConfig.from_env()

    client = ControlPlaneClient(cfg.control_plane_url)
    secret = cfg.load_secret()
    if secret is None:
        log.info("no stored secret - enrolling with one-time token")
        out = client.enroll(cfg.enrollment_token)
        cfg.save_secret(out["agent_id"], out["agent_secret"])
        log.info("enrolled as agent %s", out["agent_id"])
    else:
        client.agent_id, client.agent_secret = secret

    provisioner = Provisioner(build_backend(cfg))
    agent = Agent(client, provisioner, heartbeat_interval=cfg.heartbeat_interval)
    log.info("agent up: env=%s backend=%s, heartbeating every %ss",
             cfg.environment, cfg.backend, cfg.heartbeat_interval)
    agent.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
