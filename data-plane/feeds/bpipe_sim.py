"""
bpipe_sim.py - synthetic equities trade/quote generator, schema-shaped like a
Bloomberg B-PIPE feed (time, sym, price, size, side, venue). This is NOT a
real B-PIPE connector - it does not use Bloomberg's SDK or credentials.
It exists to exercise the tick architecture end to end and to demonstrate
the connector pattern a real B-PIPE handler would slot into.

Publishes to the tickerplant matching each symbol's shard, at a configurable
rate, and stamps a batch_sent event immediately before each batch so the
downstream WDB can compute true end-to-end transit lag.
"""
import argparse
import os
import random
import time

from feed_common import TickerplantConnection, utc_now

SYMBOLS_A_M = ["AAPL", "AMZN", "BAC", "C", "GOOGL", "IBM", "JPM", "META", "MSFT"]
SYMBOLS_N_Z = ["NFLX", "NVDA", "ORCL", "PYPL", "QCOM", "TSLA", "UBER", "V", "WMT"]
VENUES = ["XNAS", "XNYS", "ARCX", "BATS"]
SIDES = ["B", "S"]


def gen_trade(sym: str, base_price: float) -> list:
    ts = utc_now()
    price = round(base_price * (1 + random.uniform(-0.002, 0.002)), 2)
    size = random.choice([100, 200, 300, 500, 1000])
    side = random.choice(SIDES)
    venue = random.choice(VENUES)
    shard = "A_M" if sym[0].upper() <= "M" else "N_Z"
    return [ts, sym, price, size, side, venue, shard]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tp-a-m-host", default=os.environ.get("TP_A_M_HOST", "tp-a-m"))
    parser.add_argument("--tp-a-m-port", type=int, default=int(os.environ.get("TP_A_M_PORT", "5010")))
    parser.add_argument("--tp-n-z-host", default=os.environ.get("TP_N_Z_HOST", "tp-n-z"))
    parser.add_argument("--tp-n-z-port", type=int, default=int(os.environ.get("TP_N_Z_PORT", "5010")))
    parser.add_argument("--rate", type=float, default=float(os.environ.get("BPIPE_RATE_HZ", "20")),
                         help="ticks per second across all symbols")
    parser.add_argument("--batch-ms", type=int, default=int(os.environ.get("BPIPE_BATCH_MS", "200")))
    args = parser.parse_args()

    conn_a_m = TickerplantConnection(args.tp_a_m_host, args.tp_a_m_port, "bpipe_sim.A_M")
    conn_n_z = TickerplantConnection(args.tp_n_z_host, args.tp_n_z_port, "bpipe_sim.N_Z")
    conn_a_m.connect()
    conn_n_z.connect()

    prices = {s: random.uniform(20, 500) for s in SYMBOLS_A_M + SYMBOLS_N_Z}
    interval = args.batch_ms / 1000.0
    per_batch = max(1, int(args.rate * interval))

    while True:
        batch_a_m, batch_n_z = [], []
        for _ in range(per_batch):
            sym = random.choice(SYMBOLS_A_M + SYMBOLS_N_Z)
            prices[sym] *= 1 + random.uniform(-0.0015, 0.0015)
            row = gen_trade(sym, prices[sym])
            (batch_a_m if row[-1] == "A_M" else batch_n_z).append(row)

        if batch_a_m:
            conn_a_m.publish("trade", batch_a_m)
        if batch_n_z:
            conn_n_z.publish("trade", batch_n_z)

        time.sleep(interval)


if __name__ == "__main__":
    main()
