"""
api_client.py - a tiny stdlib-only HTTP client for the control-api.

Deliberately no `requests` dependency: the demo has to run on a bare box in
front of a client with nothing pip-installed but what the repo already needs.
urllib is ugly but everywhere.

The DemoRunner depends on the small surface defined here (ControlApi), not on
this concrete class, so tests inject a fake and never open a socket.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Protocol


class ControlApi(Protocol):
    """The exact surface DemoRunner needs - fake this in tests."""

    def login(self, email: str, password: str) -> None: ...
    def health(self) -> dict: ...
    def topology_status(self) -> dict: ...           # {service_name: "running"/"exited"/...}
    def stop_service(self, service: str) -> dict: ...
    def list_connectors(self) -> list: ...
    def toggle_connector(self, connector_id: int) -> dict: ...  # flips current state
    def metrics_snapshot(self) -> dict: ...
    def audit(self, limit: int = 20, action: str | None = None) -> list: ...


class HttpControlApi:
    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token: str | None = None

    def _req(self, method: str, path: str, body: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}") from e

    def login(self, email: str, password: str) -> None:
        out = self._req("POST", "/auth/login", {"email": email, "password": password})
        self.token = out["access_token"]

    def health(self) -> dict:
        return self._req("GET", "/health")

    def topology_status(self) -> dict:
        return self._req("GET", "/topology/status")

    def stop_service(self, service: str) -> dict:
        return self._req("POST", f"/topology/service/{service}/stop")

    def list_connectors(self) -> list:
        return self._req("GET", "/connectors")

    def toggle_connector(self, connector_id: int) -> dict:
        # the endpoint flips whatever the connector's current state is
        return self._req("POST", f"/connectors/{connector_id}/toggle")

    def metrics_snapshot(self) -> dict:
        return self._req("GET", "/metrics/snapshot")

    def audit(self, limit: int = 20, action: str | None = None) -> list:
        q = f"/audit?limit={limit}"
        if action:
            q += f"&action={action}"
        return self._req("GET", q)
