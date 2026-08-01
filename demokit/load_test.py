"""
load_test.py - CLI: throughput ramp + slow-subscriber discard against a live
data plane.  `python -m demokit.load_test --help`

Two modes:

  throughput  (default)  ramp offered load up in steps, report achieved
                         ingest and where the data plane starts shedding.
  slow-sub               attach a deliberately-slow subscriber and watch the
                         tickerplant auto-discard it, printing the queue
                         growth -> strikes -> discard timeline.

Nothing here can run in a repo checkout without KDB-X + a running stack; that
is the point of keeping the arithmetic in demokit.harness (which IS tested).
The numbers this prints are the ones you measured on your box - the README is
explicit that you never quote a throughput figure you didn't produce here
yourself.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict

from . import harness
from .kdb_probe import GatewayRowCountProbe, TickerplantSubProbe
from .feed_publisher import FeedPublisher, make_trade_generator, SlowSubscriber

log = logging.getLogger("demokit.load_test")


def _fmt_row(r: harness.StepResult) -> str:
    flag = "ok " if r.kept_up else "SHED"
    return (f"  {r.target_rps:>8,} | {r.achieved_publish_rps:>12,.0f} | "
            f"{r.achieved_ingest_rps:>12,.0f} | {r.loss_pct:>6.1f}% | {flag}")


def _print_report(report: harness.LoadReport) -> None:
    print("\n  offered rps |  published/s |    ingest/s  |  loss  | verdict")
    print("  " + "-" * 62)
    for r in report.steps:
        print(_fmt_row(r))
    print("  " + "-" * 62)
    peak = report.peak_sustained_rps
    print(f"\n  peak sustained ingest (loss <= 2%): {peak:,.0f} rows/s")
    shed = report.first_shed_step
    if shed is not None:
        print(f"  first shed at offered {shed.target_rps:,} rps "
              f"({shed.loss_pct:.1f}% loss)")
    else:
        print("  no shedding observed across the whole ramp - raise --stop-rps "
              "to find the ceiling")


def _write_report(report: harness.LoadReport, path: str) -> None:
    if path.endswith(".json"):
        payload = {
            "steps": [
                {**asdict(s),
                 "achieved_publish_rps": round(s.achieved_publish_rps, 1),
                 "achieved_ingest_rps": round(s.achieved_ingest_rps, 1),
                 "loss_pct": round(s.loss_pct, 2),
                 "kept_up": s.kept_up}
                for s in report.steps
            ],
            "peak_sustained_ingest_rps": round(report.peak_sustained_rps, 1),
            "total_published": report.total_published,
            "total_ingested": report.total_ingested,
        }
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=2)
    else:  # markdown
        lines = ["# Load-test result", "",
                 f"Peak sustained ingest (loss <= 2%): **{report.peak_sustained_rps:,.0f} rows/s**",
                 "",
                 "| offered rps | published/s | ingest/s | loss | verdict |",
                 "|---:|---:|---:|---:|:--|"]
        for s in report.steps:
            lines.append(f"| {s.target_rps:,} | {s.achieved_publish_rps:,.0f} | "
                         f"{s.achieved_ingest_rps:,.0f} | {s.loss_pct:.1f}% | "
                         f"{'ok' if s.kept_up else 'SHED'} |")
        with open(path, "w") as fh:
            fh.write("\n".join(lines) + "\n")
    print(f"\n  report written to {path}")


def run_throughput(args) -> int:
    steps = harness.ramp(args.start_rps, args.stop_rps, args.step_rps,
                         args.step_seconds, batch_ms=args.batch_ms)
    print(f"\n  ramp: {args.start_rps:,} -> {args.stop_rps:,} rps in "
          f"{args.step_rps:,} steps, {args.step_seconds:.0f}s each, "
          f"{len(steps)} steps, {args.shards} shards")

    publisher = FeedPublisher(args.shards, tp_host_pattern=args.tp_host_pattern,
                              tp_port=args.tp_port)
    probe = GatewayRowCountProbe(args.gateway_host, args.gateway_port)
    generate = make_trade_generator(args.shards)

    def on_step(r: harness.StepResult):
        print(_fmt_row(r))

    print("\n  offered rps |  published/s |    ingest/s  |  loss  | verdict")
    print("  " + "-" * 62)
    try:
        report = harness.run_profile(steps, publisher, probe, generate, on_step=on_step)
    finally:
        publisher.close()
        probe.close()
    _print_report(report)
    if args.report:
        _write_report(report, args.report)
    return 0


def run_slow_sub(args) -> int:
    probe = TickerplantSubProbe(args.tp_host, args.tp_port)
    sub = SlowSubscriber(args.tp_host, args.tp_port, read_every_s=args.read_every_s)

    print(f"\n  attaching slow subscriber to {args.tp_host}:{args.tp_port} "
          f"(reads every {args.read_every_s:.0f}s)")
    print("  make sure a feed is running so the TP has trades to push.\n")

    import threading
    t = threading.Thread(target=sub.run, args=(args.window_seconds,), daemon=True)
    t.start()

    start = time.monotonic()
    baseline_discards = probe.discarded_count()
    print("  elapsed | queued bytes (this sub) | strikes | discarded")
    print("  " + "-" * 58)
    while t.is_alive() and (time.monotonic() - start) < args.window_seconds + 5:
        time.sleep(2.0)
        stats = probe.sub_stats()
        queued = strikes = 0
        for row in stats or []:
            if isinstance(row, dict):
                queued = max(queued, int(row.get("bytes", 0) or 0))
                strikes = max(strikes, int(row.get("strikes", 0) or 0))
        discarded = probe.discarded_count() - baseline_discards
        print(f"  {time.monotonic() - start:>6.0f}s | {queued:>22,} | "
              f"{strikes:>7} | {discarded:>9}")
        if discarded > 0:
            print("\n  the tickerplant auto-discarded the slow subscriber. "
                  "Check the control-plane Audit tab for the matching event.")
            break
    probe.close()
    sub.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m demokit.load_test",
                                description="Load-test + slow-subscriber demo for the kdb+ control plane")
    sub = p.add_subparsers(dest="mode")

    t = sub.add_parser("throughput", help="ramp offered load and report achieved ingest")
    t.add_argument("--shards", type=int, default=2)
    t.add_argument("--start-rps", type=int, default=1000)
    t.add_argument("--stop-rps", type=int, default=10000)
    t.add_argument("--step-rps", type=int, default=1000)
    t.add_argument("--step-seconds", type=float, default=20.0)
    t.add_argument("--batch-ms", type=int, default=100)
    t.add_argument("--tp-host-pattern", default="tp-{shard}",
                   help="how to reach each shard's TP, e.g. 'tp-{shard}' or 'localhost'")
    t.add_argument("--tp-port", type=int, default=5010)
    t.add_argument("--gateway-host", default="localhost")
    t.add_argument("--gateway-port", type=int, default=5050)
    t.add_argument("--report", help="write result to this .json or .md file")

    s = sub.add_parser("slow-sub", help="attach a slow subscriber and watch it get discarded")
    s.add_argument("--tp-host", default="localhost")
    s.add_argument("--tp-port", type=int, default=5010)
    s.add_argument("--read-every-s", type=float, default=5.0)
    s.add_argument("--window-seconds", type=float, default=120.0)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    if args.mode == "slow-sub":
        return run_slow_sub(args)
    if args.mode in (None, "throughput"):
        # default to throughput with defaults if no subcommand given
        if args.mode is None:
            args = build_parser().parse_args(["throughput", *(argv or [])])
        return run_throughput(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
