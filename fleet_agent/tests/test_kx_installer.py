"""
Tests for the KX portal download flow. Key guarantees:
- the bearer token NEVER appears in plan() (only the '$KX_BEARER_TOKEN' placeholder)
- preflight requires the token to be present in the environment
- the portal download URL is built from version/channel/arch
"""
import pytest

from fleet_agent.kx_installer import KxInstaller, KxInstallConfig, kx_arch_for_os

_FAKE_TOKEN = "SECRET-TOKEN-VALUE-should-never-appear"


def _portal_cfg(**kw):
    kw.setdefault("license_path", "/run/secrets/kc.lic")
    return KxInstallConfig(source="kx-portal",
                           kx_version="4.1", kx_channel="~latest~", kx_arch="l64", **kw)


def test_portal_plan_has_auth_download_and_unpack():
    labels = [l for l, _ in KxInstaller(_portal_cfg()).plan()]
    assert labels[0] == "make install dir"
    assert "verify KX portal token" in labels
    assert "download KX binary" in labels
    assert "unpack" in labels
    assert "install licence" in labels
    assert labels[-1] == "verify"


def test_portal_plan_never_contains_the_real_token(monkeypatch):
    monkeypatch.setenv("KX_BEARER_TOKEN", _FAKE_TOKEN)
    flat = " ".join(" ".join(argv) for _l, argv in KxInstaller(_portal_cfg()).plan())
    assert _FAKE_TOKEN not in flat            # token must never be baked into the plan
    assert "$KX_BEARER_TOKEN" in flat         # only the placeholder appears


def test_portal_download_url_built_from_version_channel_arch():
    inst = KxInstaller(_portal_cfg())
    assert inst.download_url() == \
        "https://portal.dl.kx.com/assets/raw/kdb+/4.1/~latest~/l64.zip"
    download = next(argv for l, argv in inst.plan() if l == "download KX binary")
    assert inst.download_url() in download
    assert "--oauth2-bearer" in download


def test_portal_preflight_requires_token_in_env(monkeypatch):
    monkeypatch.delenv("KX_BEARER_TOKEN", raising=False)
    problems = KxInstaller(_portal_cfg()).preflight()
    assert any("bearer token" in p for p in problems)

    monkeypatch.setenv("KX_BEARER_TOKEN", _FAKE_TOKEN)
    assert KxInstaller(_portal_cfg()).preflight() == []


def test_portal_preflight_still_needs_linux_and_licence(monkeypatch):
    monkeypatch.setenv("KX_BEARER_TOKEN", _FAKE_TOKEN)
    problems = KxInstaller(_portal_cfg(os_type="windows", license_path="")).preflight()
    assert any("linux" in p for p in problems)
    assert any("licence" in p for p in problems)


def test_url_source_still_works():
    cfg = KxInstallConfig(source="url", binary_url="https://mirror.example.com/kx.tgz",
                          license_path="/run/secrets/kc.lic")
    labels = [l for l, _ in KxInstaller(cfg).plan()]
    assert "download KX binary" in labels
    download = next(argv for l, argv in KxInstaller(cfg).plan() if l == "download KX binary")
    assert "https://mirror.example.com/kx.tgz" in download


def test_from_env_builds_portal_config_without_storing_token():
    env = {"KX_INSTALL_SOURCE": "kx-portal", "KX_VERSION": "4.1", "KX_CHANNEL": "~latest~",
           "KX_ARCH": "l64", "KX_LICENSE_PATH": "/run/secrets/kc.lic",
           "KX_BEARER_TOKEN": _FAKE_TOKEN}
    cfg = KxInstallConfig.from_env(env)
    assert cfg.source == "kx-portal" and cfg.kx_version == "4.1"
    assert cfg.license_path == "/run/secrets/kc.lic"
    assert cfg.bearer_env == "KX_BEARER_TOKEN"
    # the token value is NOT copied onto the config anywhere
    assert _FAKE_TOKEN not in repr(cfg)
    assert KxInstaller(cfg).download_url().endswith("/4.1/~latest~/l64.zip")


# ---- OS -> KX arch resolver (docker chooses the binary by target OS) -------

import pytest


@pytest.mark.parametrize("os_type,arch", [
    ("ubuntu-22.04", "l64"),
    ("rhel-9", "l64"),
    ("amazonlinux-2023", "l64"),
    ("ubuntu-22.04-arm64", "l64arm"),
    ("linux-aarch64", "l64arm"),
    ("amazonlinux-2023-graviton", "l64arm"),
    ("macos", "m64"),
    ("macos-arm", "mac-arm"),
    ("windows-2022", "w64"),
])
def test_kx_arch_for_os(os_type, arch):
    assert kx_arch_for_os(os_type) == arch


def test_installer_resolves_arch_from_os_when_not_pinned():
    inst = KxInstaller(KxInstallConfig(source="url", binary_url="https://mirror.example.com/kx.tgz",
                                       license_path="/run/secrets/kc.lic",
                                       os_type="ubuntu-22.04-arm64"))
    assert inst.arch() == "l64arm"
    # verify + chmod steps target the resolved arch dir
    verify = next(argv for l, argv in inst.plan() if l == "verify")
    assert "/opt/kx/q/l64arm/q" in verify


def test_unknown_source_is_a_preflight_problem():
    # "local" (a pre-staged folder on this one box) used to be a third valid
    # source - removed entirely, so it must now be rejected the same way any
    # other unrecognized value is, not silently accepted.
    problems = KxInstaller(KxInstallConfig(source="local", license_path="/run/secrets/kc.lic")).preflight()
    assert any("unknown source" in p for p in problems)


def test_pinned_arch_overrides_os_resolution():
    inst = KxInstaller(KxInstallConfig(source="url", binary_url="https://mirror.example.com/kx.tgz",
                                       license_path="/x", os_type="ubuntu-22.04", kx_arch="l64arm"))
    assert inst.arch() == "l64arm"
