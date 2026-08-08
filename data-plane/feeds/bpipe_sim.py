"""
bpipe_sim.py - synthetic equities trade generator, schema-shaped like a
Bloomberg B-PIPE feed (time, sym, price, size, side, venue). This is NOT a
real B-PIPE connector - it does not use Bloomberg's SDK or credentials.
It exists to exercise the tick architecture end to end and to demonstrate
the connector pattern a real B-PIPE handler would slot into.

Fans out across however many shards SHARD_COUNT defines: each symbol is
routed to its owning shard's tickerplant by topology.shard_of, the same
partition the gateway and control plane use.
"""
import argparse
import logging
import os
import random
import time

import topology
from feed_common import ShardedPublisher, build_universe, utc_now

# one universe spread across the alphabet so every shard gets traffic at
# higher shard counts (A-G / H-N / O-T / U-Z at N=4, etc.)
SYMBOLS = ["AAPL", "AMZN", "BAC", "C", "GOOGL", "IBM", "JPM", "META", "MSFT",
           "NFLX", "NVDA", "ORCL", "PYPL", "QCOM", "TSLA", "UBER", "V", "WMT"]
VENUES = ["XNAS", "XNYS", "ARCX", "BATS"]
SIDES = ["B", "S"]


def gen_trade(sym: str, base_price: float, shard_count: int) -> list:
    ts = utc_now()
    price = round(base_price * (1 + random.uniform(-0.002, 0.002)), 2)
    size = random.choice([100, 200, 300, 500, 1000])
    side = random.choice(SIDES)
    venue = random.choice(VENUES)
    shard = topology.shard_of(sym, shard_count)          # "s0", "s1", ...
    return [ts, sym, price, size, side, venue, shard]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shards", type=int, default=int(os.environ.get("SHARD_COUNT", "2")))
    parser.add_argument("--rate", type=float, default=float(os.environ.get("BPIPE_RATE_HZ", "20")),
                        help="ticks per second across all symbols")
    parser.add_argument("--batch-ms", type=int, default=int(os.environ.get("BPIPE_BATCH_MS", "200")))
    parser.add_argument("--symbols", default=os.environ.get("BPIPE_SYMBOLS", ""),
                        help="explicit comma-separated symbol list (overrides the built-in set)")
    parser.add_argument("--symbols-file", default=os.environ.get("BPIPE_SYMBOLS_FILE", ""),
                        help="path to a file of symbols (comma or newline separated)")
    parser.add_argument("--symbol-count", type=int, default=int(os.environ.get("SIM_SYMBOL_COUNT", "0")),
                        help="grow/trim the universe to this many symbols (pads with synthetic tickers)")
    args = parser.parse_args()

    universe = build_universe(args.symbol_count, args.symbols, args.symbols_file, SYMBOLS)
    logging.getLogger("bpipe_sim").info("simulating %d symbols across %d shards", len(universe), args.shards)

    pub = ShardedPublisher("bpipe_sim", shard_count=args.shards)
    pub.connect()

    prices = {s: random.uniform(20, 500) for s in universe}
    interval = args.batch_ms / 1000.0
    per_batch = max(1, int(args.rate * interval))

    while True:
        try:
            batch = []
            for _ in range(per_batch):
                sym = random.choice(universe)
                prices[sym] *= 1 + random.uniform(-0.0015, 0.0015)
                batch.append(gen_trade(sym, prices[sym], args.shards))
            if batch:
                pub.publish_rows("trade", batch)
        except Exception:  # noqa: BLE001 - keep the feed alive; log and carry on
            logging.getLogger("bpipe_sim").exception("publish cycle failed, continuing")
        time.sleep(interval)


if __name__ == "__main__":
    main()
