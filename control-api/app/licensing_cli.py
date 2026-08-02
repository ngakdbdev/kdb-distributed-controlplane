"""
licensing_cli.py - mint and validate product licence keys.

  python -m app.licensing_cli trial
  python -m app.licensing_cli mint --edition enterprise --days 365
  python -m app.licensing_cli check <KEY>

Run from control-api/ (so `app` imports). Minting uses LICENSE_SIGNING_SECRET
if set - use the SAME secret your deployment validates with.
"""
from __future__ import annotations

import argparse
import sys

from app import licensing


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.licensing_cli")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("trial", help="mint a 30-day trial key")

    m = sub.add_parser("mint", help="mint a key")
    m.add_argument("--edition", default="standard", choices=["trial", "standard", "enterprise"])
    m.add_argument("--days", type=int, default=365)

    c = sub.add_parser("check", help="validate a key")
    c.add_argument("key")

    args = p.parse_args(argv)
    if args.cmd == "trial":
        print(licensing.mint_trial())
        return 0
    if args.cmd == "mint":
        days = licensing.TRIAL_DAYS if args.edition == "trial" else args.days
        print(licensing.mint(edition=args.edition, valid_days=days))
        return 0
    if args.cmd == "check":
        info = licensing.validate(args.key)
        print(licensing.status_line(info))
        return 0 if info.valid else 1
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
