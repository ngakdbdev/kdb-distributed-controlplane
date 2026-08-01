"""
config.py - agent configuration from environment variables, plus persistence of
the agent secret it receives at enrollment (so a restart re-uses it instead of
trying to enroll again with a spent one-time token).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentConfig:
    control_plane_url: str
    enrollment_token: str
    environment: str            # aws | azure | gcp | onprem (informational)
    backend: str                # helm | compose
    secret_file: str
    heartbeat_interval: float
    # helm backend
    helm_release: str
    helm_chart: str
    helm_namespace: str
    # compose backend
    project_dir: str
    python: str

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            control_plane_url=os.environ["CONTROL_PLANE_URL"],
            enrollment_token=os.environ.get("ENROLLMENT_TOKEN", ""),
            environment=os.environ.get("AGENT_ENVIRONMENT", "onprem"),
            backend=os.environ.get("AGENT_BACKEND", "helm"),
            secret_file=os.environ.get("AGENT_SECRET_FILE", "/var/lib/fleet-agent/secret.json"),
            heartbeat_interval=float(os.environ.get("HEARTBEAT_INTERVAL", "10")),
            helm_release=os.environ.get("HELM_RELEASE", "kdb-control-plane"),
            helm_chart=os.environ.get("HELM_CHART", "helm/kdb-control-plane"),
            helm_namespace=os.environ.get("HELM_NAMESPACE", "default"),
            project_dir=os.environ.get("PROJECT_DIR", "."),
            python=os.environ.get("PYTHON_BIN", "python3"),
        )

    def load_secret(self) -> Optional[tuple]:
        if not os.path.exists(self.secret_file):
            return None
        try:
            with open(self.secret_file) as fh:
                d = json.load(fh)
            return int(d["agent_id"]), d["agent_secret"]
        except Exception:  # noqa: BLE001
            return None

    def save_secret(self, agent_id: int, agent_secret: str) -> None:
        os.makedirs(os.path.dirname(self.secret_file) or ".", exist_ok=True)
        with open(self.secret_file, "w") as fh:
            json.dump({"agent_id": agent_id, "agent_secret": agent_secret}, fh)
        os.chmod(self.secret_file, 0o600)
