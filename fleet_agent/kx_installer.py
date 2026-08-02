"""
kx_installer.py - install the KX (KDB-X / q) binary on a Linux target and
activate it with YOUR licence, as part of provisioning.

HONEST + LEGAL BOUNDARY:
- We never bundle or redistribute KX's binary or a licence file - KX's terms
  forbid that. The installer PULLS the binary from a source YOU configure
  (your artifact store / mirror / KX download you're entitled to) and installs
  the licence from a file/secret path YOU provide at deploy time.
- Never put a licence in code, an image, or a chat. Point AGENT at a secret.

The `plan()` (ordered, side-effect-free command list) is unit-tested; `install()`
performs the real download/placement/verify and only works on a real Linux box
with a reachable binary URL and a licence file - it isn't run in CI.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass

log = logging.getLogger("fleet_agent.kx_installer")


@dataclass
class KxInstallConfig:
    binary_url: str                 # where to pull the q binary/tarball from (your source)
    license_path: str               # path to k4.lic / kc.lic on the box (a mounted secret)
    install_dir: str = "/opt/kx"
    qhome: str = "/opt/kx/q"
    os_type: str = "linux"          # linux-based target (per requirement)


class KxInstaller:
    def __init__(self, cfg: KxInstallConfig):
        self.cfg = cfg

    def plan(self) -> list:
        """The ordered steps, as (label, argv) pairs. Pure - no side effects."""
        q = self.cfg.qhome
        return [
            ("make install dir", ["mkdir", "-p", self.cfg.install_dir, q]),
            ("download KX binary", ["curl", "-fsSL", "-o",
                                    f"{self.cfg.install_dir}/kx.tgz", self.cfg.binary_url]),
            ("unpack", ["tar", "-xzf", f"{self.cfg.install_dir}/kx.tgz", "-C", q,
                        "--strip-components", "1"]),
            ("install licence", ["cp", self.cfg.license_path, f"{q}/k4.lic"]),
            ("mark q executable", ["chmod", "+x", f"{q}/l64/q"]),
            ("verify", [f"{q}/l64/q", "-c", "1", "1"]),   # trivial exit to prove q + licence load
        ]

    def preflight(self) -> list:
        """Problems that would make install fail, checked without doing it."""
        problems = []
        if self.cfg.os_type != "linux":
            problems.append(f"target os '{self.cfg.os_type}' is not linux (only linux is supported)")
        if not self.cfg.binary_url:
            problems.append("no binary_url configured (set where to pull the KX binary from)")
        if not self.cfg.license_path:
            problems.append("no license_path configured (mount your KX licence as a secret)")
        return problems

    def install(self) -> dict:
        """Run the plan on a real Linux box. Not exercised in CI."""
        problems = self.preflight()
        if problems:
            return {"ok": False, "problems": problems}
        if not os.path.exists(self.cfg.license_path):
            return {"ok": False, "problems": [f"licence not found at {self.cfg.license_path}"]}

        ran = []
        for label, argv in self.plan():
            log.info("kx-install: %s", label)
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=600)
            ran.append(label)
            if proc.returncode != 0:
                return {"ok": False, "failed_step": label,
                        "detail": (proc.stderr or proc.stdout)[-500:], "ran": ran}
        return {"ok": True, "qhome": self.cfg.qhome, "ran": ran}
