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
import os
import random
import time

import topology
from feed_common import ShardedPublisher, utc_now

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
    args = parser.parse_args()

    pub = ShardedPublisher("bpipe_sim", shard_count=args.shards)
    pub.connect()

    prices = {s: random.uniform(20, 500) for s in SYMBOLS}
    interval = args.batch_ms / 1000.0
    per_batch = max(1, int(args.rate * interval))

    while True:
        batch = []
        for _ in range(per_batch):
            sym = random.choice(SYMBOLS)
            prices[sym] *= 1 + random.uniform(-0.0015, 0.0015)
            batch.append(gen_trade(sym, prices[sym], args.shards))
        if batch:
            pub.publish_rows("trade", batch)
        time.sleep(interval)


if __name__ == "__main__":
    main()
