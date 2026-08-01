"""
runner.py - export kdb+ data to a destination.

  # list destinations
  python -m export.runner --list

  # HDB history -> local Parquet (works offline, no cloud account)
  python -m export.runner --host hdb-s0 --port 5060 \
      --table trade --date 2026.08.01 --symbols AAPL,MSFT \
      --sink parquet --out /tmp/export

  # recent data from the gateway -> Snowflake (needs SNOWFLAKE_* env)
  python -m export.runner --source gateway --host gateway --port 5050 \
      --table trade --sink snowflake

Source is just which q process you point at: the gateway (recent data across all
shards), a specific shard's RDB (today) or HDB (history), etc. Credentialed
sinks (snowflake/databricks/fabric) read their config from env and refuse with a
clear message if it's missing - they never pretend to have written.
"""
from __future__ import annotations

import argparse
import logging
import sys

from . import catalog, get_sink
from .extractor import KdbExtractor
from .job import ExportJob
from .sinks.base import SinkNotConfigured, SinkError

_DEFAULT_PORTS = {"gateway": 5050, "rdb": 5020, "idb": 5030, "hdb": 5060}


def _print_catalog() -> None:
    print("\n  sink         offline?  requires")
    print("  " + "-" * 66)
    for s in catalog():
        print(f"  {s['name']:<12} {'yes' if s['offline'] else 'no ':<9} {s['requires']}")
    print("\n  offline = runs with no cloud account (parquet). Others read *_ config from env.\n")


def _build_sink(name: str, args):
    cls = get_sink(name)
    if name == "parquet":
        if not args.out:
            print("  parquet sink needs --out <dir>")
            return None
        return cls(out_dir=args.out, compression=args.compression)
    return cls(config=None)  # snowflake/databricks/fabric read env


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m export.runner")
    p.add_argument("--list", action="store_true", help="list destinations and exit")
    p.add_argument("--source", default="gateway",
                   help="which q process: gateway / rdb / idb / hdb (used for the default port)")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--table", default="trade")
    p.add_argument("--date", help="q date literal for HDB history, e.g. 2026.08.01")
    p.add_argument("--symbols", help="comma-separated; omitted = all")
    p.add_argument("--where", help="extra raw q predicate (advanced)")
    p.add_argument("--sink", help="parquet / snowflake / databricks / fabric")
    p.add_argument("--out", help="output dir (parquet sink)")
    p.add_argument("--compression", default="snappy")
    p.add_argument("--batch-rows", type=int, default=500_000)
    args = p.parse_args(argv)

    if args.list or not args.sink:
        _print_catalog()
        return 0

    try:
        sink = _build_sink(args.sink, args)
    except KeyError as exc:
        print(f"  {exc}")
        return 2
    if sink is None:
        return 2

    port = args.port or _DEFAULT_PORTS.get(args.source, 5050)
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None

    from qpython import qconnection
    conn = qconnection.QConnection(host=args.host, port=port, pandas=False)
    conn.open()

    extractor = KdbExtractor(conn, batch_rows=args.batch_rows)
    job = ExportJob(extractor, sink)
    print(f"\n  exporting {args.table} from {args.host}:{port} -> {args.sink}\n")
    try:
        report = job.run(args.table, date=args.date, symbols=symbols, where=args.where)
    except SinkNotConfigured as exc:
        print(f"\n  {args.sink} sink isn't configured:\n    {exc}\n")
        return 3
    except SinkError as exc:
        print(f"\n  export failed: {exc}\n")
        return 2
    finally:
        conn.close()

    print(f"\n  {report.summary()}")
    print(f"  target: {report.target}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
