"""
crims_sim.py - synthetic risk/reference-data generator, schema-shaped like a
CRIMS-style risk feed (time, sym, riskType, limit, exposure, status). Not a
real CRIMS connector - demonstrates the connector pattern and exercises the
tick architecture with a second, lower-rate, differently-shaped source.

Fans out across SHARD_COUNT shards via topology.shard_of, same as bpipe_sim.
"""
import argparse
import logging
import os
import random
import time

import topology
from feed_common import ShardedPublisher, utc_now

SYMBOLS = ["AAPL", "AMZN", "BAC", "C", "GOOGL", "IBM", "JPM", "META", "MSFT",
           "NFLX", "NVDA", "ORCL", "PYPL", "QCOM", "TSLA", "UBER", "V", "WMT"]
RISK_TYPES = ["VAR", "GROSS_EXPOSURE", "NET_EXPOSURE", "CONCENTRATION"]
STATUSES = ["OK", "WARN", "BREACH"]
STATUS_WEIGHTS = [0.85, 0.12, 0.03]


def gen_risk(sym: str, shard_count: int) -> list:
    ts = utc_now()
    risk_type = random.choice(RISK_TYPES)
    limit = round(random.uniform(1_000_000, 50_000_000), 2)
    exposure = round(limit * random.uniform(0.1, 1.15), 2)
    status = random.choices(STATUSES, weights=STATUS_WEIGHTS)[0]
    shard = topology.shard_of(sym, shard_count)
    return [ts, sym, risk_type, limit, exposure, status, shard]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shards", type=int, default=int(os.environ.get("SHARD_COUNT", "2")))
    parser.add_argument("--rate", type=float, default=float(os.environ.get("CRIMS_RATE_HZ", "2")),
                        help="records per second across all symbols")
    parser.add_argument("--batch-ms", type=int, default=int(os.environ.get("CRIMS_BATCH_MS", "1000")))
    args = parser.parse_args()

    pub = ShardedPublisher("crims_sim", shard_count=args.shards)
    pub.connect()

    interval = args.batch_ms / 1000.0
    per_batch = max(1, int(args.rate * interval))

    while True:
        try:
            batch = [gen_risk(random.choice(SYMBOLS), args.shards) for _ in range(per_batch)]
            if batch:
                pub.publish_rows("risk", batch)
        except Exception:  # noqa: BLE001 - keep the feed alive; log and carry on
            logging.getLogger("crims_sim").exception("publish cycle failed, continuing")
        time.sleep(interval)


if __name__ == "__main__":
    main()
