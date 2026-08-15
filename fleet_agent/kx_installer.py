"""
kx_installer.py - install the KX (KDB-X / q) binary on a Linux target and
activate it with YOUR licence, as part of provisioning.

Two download sources:
  * "kx-portal" - pull from portal.dl.kx.com with a BEARER TOKEN you supply at
    run time via an env var (default KX_BEARER_TOKEN) or a mounted secret. The
    installer checks the token against /auth/me, then downloads
    /assets/raw/kdb+/<version>/<channel>/<arch>.zip.
  * "url" - pull a tarball from any URL you configure (mirror / artifact store).

HONEST + LEGAL + SECURITY BOUNDARY:
- We never bundle or redistribute KX's binary or a licence - KX's terms forbid
  it. The installer pulls what YOU are entitled to, with YOUR credentials.
- The bearer token and the licence are SECRETS. They are read from the
  environment / a mounted file at run time. They are NEVER stored in the spec,
  written to code, logged, or returned in output (the token is substituted into
  the curl argv only at execution and scrubbed from any error text). Never paste
  a token or licence into code, an image, or a chat.

`plan()` is a secret-free, side-effect-free command list (the token appears only
as the placeholder "$KX_BEARER_TOKEN") and is unit-tested; `install()` performs
the real download/verify on a Linux box and isn't run in CI.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass

log = logging.getLogger("fleet_agent.kx_installer")

KX_PORTAL = "https://portal.dl.kx.com"


def kx_arch_for_os(os_type: str) -> str:
    """Map a target OS to the KX binary/platform code (the 'kdb version' for
    that OS). Linux x64 -> l64, Linux ARM -> l64arm, macOS -> m64/mac-arm,
    Windows -> w64. Targets are Linux-based by default."""
    o = (os_type or "").lower()
    is_arm = any(k in o for k in ("arm", "aarch64", "graviton"))
    if any(k in o for k in ("mac", "darwin", "osx")):
        return "mac-arm" if is_arm else "m64"
    if "win" in o:
        return "w64"
    return "l64arm" if is_arm else "l64"


@dataclass
class KxInstallConfig:
    license_path: str = ""              # path to kc.lic on the box (a mounted secret)
    install_dir: str = "/opt/kx"
    qhome: str = "/opt/kx/q"
    os_type: str = "linux"              # linux-based target (per requirement)
    source: str = "url"                 # "kx-portal" | "local" | "url"
    # url source
    binary_url: str = ""
    # local source: a folder of pre-staged binaries, one <arch>.zip per platform
    binaries_dir: str = ""
    # kx-portal source (token comes from env at run time, never stored here)
    portal_base: str = KX_PORTAL
    kx_version: str = "4.1"
    kx_channel: str = "~latest~"
    kx_arch: str = ""                   # blank => resolve from os_type (kx_arch_for_os)
    bearer_env: str = "KX_BEARER_TOKEN"  # NAME of the env var, not the token

    @classmethod
    def from_env(cls, env: dict | None = None) -> "KxInstallConfig":
        """Build config from environment (see .env.example). Reads only NON-secret
        knobs here; the bearer token itself is read at install() time from the env
        var named by bearer_env and is never stored on the config."""
        e = env if env is not None else os.environ
        install_dir = e.get("KX_INSTALL_DIR", "/opt/kx")
        return cls(
            license_path=e.get("KX_LICENSE_PATH", ""),
            install_dir=install_dir,
            qhome=e.get("KX_QHOME", f"{install_dir}/q"),
            os_type=e.get("KX_OS_TYPE", "linux"),
            source=e.get("KX_INSTALL_SOURCE", "url"),
            binary_url=e.get("KX_BINARY_URL", ""),
            binaries_dir=e.get("KX_BINARIES_DIR", ""),
            portal_base=e.get("KX_PORTAL_BASE", KX_PORTAL),
            kx_version=e.get("KX_VERSION", "4.1"),
            kx_channel=e.get("KX_CHANNEL", "~latest~"),
            kx_arch=e.get("KX_ARCH", ""),
            bearer_env=e.get("KX_BEARER_ENV", "KX_BEARER_TOKEN"),
        )


class KxInstaller:
    def __init__(self, cfg: KxInstallConfig):
        self.cfg = cfg

    @property
    def _token_placeholder(self) -> str:
        return f"${self.cfg.bearer_env}"

    def arch(self) -> str:
        """The KX platform code to install: explicit kx_arch, else resolved from
        the target OS."""
        return self.cfg.kx_arch or kx_arch_for_os(self.cfg.os_type)

    def download_url(self) -> str:
        c = self.cfg
        return f"{c.portal_base}/assets/raw/kdb+/{c.kx_version}/{c.kx_channel}/{self.arch()}.zip"

    def plan(self) -> list:
        """Ordered (label, argv) steps. SECRET-FREE: the bearer token appears
        only as the placeholder '$KX_BEARER_TOKEN', substituted at run time."""
        c = self.cfg
        q = c.qhome
        arch = self.arch()
        steps = [("make install dir", ["mkdir", "-p", c.install_dir, q])]

        if c.source == "kx-portal":
            tok = self._token_placeholder
            zip_path = f"{c.install_dir}/{arch}.zip"
            steps += [
                ("verify KX portal token", ["curl", "-s", "--fail-with-body",
                                            "--oauth2-bearer", tok, f"{c.portal_base}/auth/me"]),
                ("download KX binary", ["curl", "-s", "--fail-with-body", "--oauth2-bearer", tok,
                                        "-L", "-o", zip_path, self.download_url()]),
                ("unpack", ["unzip", "-o", zip_path, "-d", c.install_dir]),
            ]
        elif c.source == "local":
            # pick the pre-staged binary for this OS/arch from the data folder
            zip_path = f"{c.binaries_dir}/{arch}.zip"
            steps += [
                (f"unpack KX binary for {arch}", ["unzip", "-o", zip_path, "-d", c.install_dir]),
            ]
        else:  # url tarball
            tgz = f"{c.install_dir}/kx.tgz"
            steps += [
                ("download KX binary", ["curl", "-fsSL", "-o", tgz, c.binary_url]),
                ("unpack", ["tar", "-xzf", tgz, "-C", q, "--strip-components", "1"]),
            ]

        steps += [
            ("install licence", ["cp", c.license_path, f"{q}/kc.lic"]),
            ("mark q executable", ["chmod", "+x", f"{q}/{arch}/q"]),
            ("verify", [f"{q}/{arch}/q", "-c", "1", "1"]),   # trivial exit proves q + licence load
        ]
        return steps

    def preflight(self) -> list:
        """Problems that would make install fail, checked without doing it.
        For kx-portal, checks the token env var is PRESENT (never reads/stores
        its value beyond that)."""
        c = self.cfg
        problems = []
        if c.os_type != "linux" and c.source != "local":
            problems.append(f"target os '{c.os_type}' is not linux (only linux is supported)")
        if not c.license_path:
            problems.append("no license_path configured (mount your KX licence as a secret)")
        if c.source == "kx-portal":
            if not os.environ.get(c.bearer_env):
                problems.append(f"no KX portal bearer token in env {c.bearer_env} "
                                "(export it or mount it as a secret at run time)")
        elif c.source == "local":
            if not c.binaries_dir:
                problems.append("no binaries_dir configured (KX_BINARIES_DIR - the data folder "
                                "holding <arch>.zip per platform)")
        elif c.source == "url":
            if not c.binary_url:
                problems.append("no binary_url configured (set where to pull the KX binary from)")
        else:
            problems.append(f"unknown source '{c.source}' (use 'kx-portal', 'local', or 'url')")
        return problems

    def install(self) -> dict:
        """Run the plan on a real Linux box. Not exercised in CI. The bearer
        token is injected into the curl argv here and scrubbed from any output."""
        problems = self.preflight()
        if problems:
            return {"ok": False, "problems": problems}
        if not os.path.exists(self.cfg.license_path):
            return {"ok": False, "problems": [f"licence not found at {self.cfg.license_path}"]}

        token = os.environ.get(self.cfg.bearer_env, "")
        placeholder = self._token_placeholder
        ran = []
        for label, argv in self.plan():
            # substitute the real token ONLY into the placeholder arg, at run time
            real = [token if a == placeholder else a for a in argv]
            log.info("kx-install: %s", label)          # label only - never the argv/token
            proc = subprocess.run(real, capture_output=True, text=True, timeout=600)
            ran.append(label)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "")[-500:]
                if token:
                    detail = detail.replace(token, "***")   # scrub token from any error text
                return {"ok": False, "failed_step": label, "detail": detail, "ran": ran}
        return {"ok": True, "qhome": self.cfg.qhome, "source": self.cfg.source, "ran": ran}
