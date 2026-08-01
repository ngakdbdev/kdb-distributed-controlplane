"""
demo.py - CLI for the narrated end-to-end walkthrough.

  python -m demokit.demo --base-url http://localhost:8000 \
      --email admin@demo-bank.local --password <pw> --pause 2

Run it live in front of a prospect (with --pause a second or two so the beats
land), or with --no-pause --no-colour in CI as a smoke test that the whole
stack - api, orchestrator, watchdog, gateway - actually works together.

Defaults match the demo tenant seeded on first boot (see control-api config:
DEMO_TENANT_ADMIN_EMAIL). It logs in as the *tenant* admin, not the platform
admin, because the connectors/metrics endpoints are tenant-scoped.
"""
from __future__ import annotations

import argparse
import logging
import sys

from .api_client import HttpControlApi
from .demo_runner import DemoRunner, Narrator


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    p = argparse.ArgumentParser(prog="python -m demokit.demo",
                                description="Narrated end-to-end demo of the kdb+ control plane")
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--email", default="admin@demo-bank.local")
    p.add_argument("--password", default="changeme",
                   help="tenant-admin password (matches DEMO_TENANT_ADMIN_PASSWORD_HASH)")
    p.add_argument("--chaos-service", default="tp-s0",
                   help="which process to kill in the self-healing act")
    p.add_argument("--heal-timeout", type=float, default=90.0)
    p.add_argument("--pause", type=float, default=1.5, help="seconds between acts")
    p.add_argument("--no-colour", action="store_true")
    p.add_argument("--no-feeds", action="store_true", help="skip the feed-enable act")
    p.add_argument("--no-chaos", action="store_true", help="skip the self-healing act")
    args = p.parse_args(argv)

    api = HttpControlApi(args.base_url)
    narrator = Narrator(colour=not args.no_colour, pause_s=args.pause)
    runner = DemoRunner(api, narrator, chaos_service=args.chaos_service,
                        heal_timeout_s=args.heal_timeout)

    try:
        result = runner.run(args.email, args.password,
                            enable_feeds=not args.no_feeds,
                            do_chaos=not args.no_chaos)
    except Exception as exc:  # noqa: BLE001 - surface a clean message to the operator
        print(f"\n  demo aborted: {exc}", file=sys.stderr)
        return 2
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
