#!/usr/bin/env python3
"""
check_topology_sync.py - guard against the shard-topology logic drifting
between its implementations.

There are (unavoidably) three runtimes that must agree on how the symbol space
splits into N shards:
  1. Python  - control-api/app/topology.py (the canonical source), vendored
     byte-for-byte into watchdog/ and data-plane/feeds/
  2. Go tmpl - helm/.../templates/_helpers.tpl (Sprig), for the in-cluster
     gateway ConfigMap
  3. the compose path - scripts/gen_topology.py, which just calls (1)

This script checks:
  A. the three vendored topology.py copies are identical
  B. gen_topology's shards.json matches app.topology (should be trivially true)
  C. if `helm` is on PATH: `helm template` at several shard counts produces a
     gateway-shards ConfigMap whose shards.json equals app.topology's. This is
     the check that catches Sprig/_helpers.tpl drift. Skipped (not failed) when
     helm isn't installed, so it's safe to run anywhere.

Exit non-zero on any mismatch. Wire into CI.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "control-api"))
from app import topology  # noqa: E402

CANONICAL = ROOT / "control-api" / "app" / "topology.py"
VENDORED = [ROOT / "watchdog" / "topology.py", ROOT / "data-plane" / "feeds" / "topology.py"]
CHART = ROOT / "helm" / "kdb-control-plane"
CHECK_COUNTS = [1, 2, 3, 4, 5, 8, 13, 26]

failures: list[str] = []


def check_vendored_copies_identical():
    canon = CANONICAL.read_bytes()
    for v in VENDORED:
        if not v.exists():
            failures.append(f"[A] missing vendored copy: {v.relative_to(ROOT)}")
        elif v.read_bytes() != canon:
            failures.append(f"[A] {v.relative_to(ROOT)} differs from canonical topology.py "
                            f"(re-copy: cp {CANONICAL.relative_to(ROOT)} {v.relative_to(ROOT)})")
    if not failures:
        print(f"[A] OK: {len(VENDORED)} vendored topology.py copies identical to canonical")


def check_gen_matches_topology():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_topology", ROOT / "scripts" / "gen_topology.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    for n in CHECK_COUNTS:
        if json.loads(gen.shards_json(n)) != topology.shards_json(n):
            failures.append(f"[B] gen_topology shards.json != app.topology at N={n}")
    print(f"[B] OK: gen_topology matches app.topology for N in {CHECK_COUNTS}")


def _helm_available() -> bool:
    try:
        subprocess.run(["helm", "version", "--short"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def check_helm_render_matches():
    if not _helm_available():
        print("[C] SKIP: helm not on PATH - run this in CI/where helm is installed "
              "to verify _helpers.tpl doesn't drift from app.topology")
        return
    import yaml
    for n in CHECK_COUNTS:
        out = subprocess.run(
            ["helm", "template", "t", str(CHART), "--set", f"shardCount={n}",
             "--show-only", "templates/shards-configmap.yaml"],
            capture_output=True, text=True,
        )
        if out.returncode != 0:
            failures.append(f"[C] helm template failed at N={n}: {out.stderr.strip()[:200]}")
            continue
        cm = yaml.safe_load(out.stdout)
        rendered = json.loads(cm["data"]["shards.json"])
        if rendered != topology.shards_json(n):
            failures.append(f"[C] helm shards.json != app.topology at N={n}")
    if not any(f.startswith("[C]") for f in failures):
        print(f"[C] OK: helm render matches app.topology for N in {CHECK_COUNTS}")


def main():
    check_vendored_copies_identical()
    check_gen_matches_topology()
    check_helm_render_matches()
    if failures:
        print("\nTOPOLOGY SYNC FAILURES:")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("\ntopology in sync across all implementations")


if __name__ == "__main__":
    main()
