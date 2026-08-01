"""
control_client.py - the agent's side of the fleet protocol, over stdlib HTTP.

Three calls, matching control-api/app/routers/fleet.py:
  enroll(token)          -> {agent_id, agent_secret}   (one-time)
  heartbeat(status)      -> [command, ...]              (pulls queued work)
  report_result(id, ...) -> ack

No third-party deps so the agent image stays tiny.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional


class ControlPlaneClient:
    def __init__(self, base_url: str, agent_id: Optional[int] = None,
                 agent_secret: Optional[str] = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.agent_secret = agent_secret
        self.timeout = timeout

    def _post(self, path: str, body: dict, secret: bool = False) -> dict:
        data = json.dumps(body).encode()
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        if secret:
            req.add_header("x-agent-secret", self.agent_secret or "")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise RuntimeError(f"POST {path} -> HTTP {e.code}: {detail}") from e

    def enroll(self, enrollment_token: str) -> dict:
        out = self._post("/fleet/enroll", {"enrollment_token": enrollment_token})
        self.agent_id = out["agent_id"]
        self.agent_secret = out["agent_secret"]
        return out

    def heartbeat(self, service_status: dict) -> list:
        out = self._post(f"/fleet/agents/{self.agent_id}/heartbeat",
                         {"service_status": service_status}, secret=True)
        return out.get("commands", [])

    def report_result(self, command_id: int, outcome: str, detail: str = "") -> dict:
        return self._post(
            f"/fleet/agents/{self.agent_id}/commands/{command_id}/result",
            {"outcome": outcome, "detail": detail}, secret=True)
