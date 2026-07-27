"""
crims_sim.py - synthetic risk/reference-data generator, schema-shaped like a
CRIMS-style risk feed (time, sym, riskType, limit, exposure, status). Not a
real CRIMS connector - demonstrates the connector pattern and exercises the
tick architecture with a second, lower-rate, differently-shaped source.
"""
import argparse
import os
import random
import time

from feed_common import TickerplantConnection, utc_now

SYMBOLS_A_M = ["AAPL", "AMZN", "BAC", "C", "GOOGL", "IBM", "JPM", "META", "MSFT"]
SYMBOLS_N_Z = ["NFLX", "NVDA", "ORCL", "PYPL", "QCOM", "TSLA", "UBER", "V", "WMT"]
RISK_TYPES = ["VAR", "GROSS_EXPOSURE", "NET_EXPOSURE", "CONCENTRATION"]
STATUSES = ["OK", "WARN", "BREACH"]
STATUS_WEIGHTS = [0.85, 0.12, 0.03]


def gen_risk(sym: str) -> list:
    ts = utc_now()
    risk_type = random.choice(RISK_TYPES)
    limit = round(random.uniform(1_000_000, 50_000_000), 2)
    exposure = round(limit * random.uniform(0.1, 1.15), 2)
    status = random.choices(STATUSES, weights=STATUS_WEIGHTS)[0]
    shard = "A_M" if sym[0].upper() <= "M" else "N_Z"
    return [ts, sym, risk_type, limit, exposure, status, shard]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tp-a-m-host", default=os.environ.get("TP_A_M_HOST", "tp-a-m"))
    parser.add_argument("--tp-a-m-port", type=int, default=int(os.environ.get("TP_A_M_PORT", "5010")))
    parser.add_argument("--tp-n-z-host", default=os.environ.get("TP_N_Z_HOST", "tp-n-z"))
    parser.add_argument("--tp-n-z-port", type=int, default=int(os.environ.get("TP_N_Z_PORT", "5010")))
    parser.add_argument("--rate", type=float, default=float(os.environ.get("CRIMS_RATE_HZ", "2")),
                         help="records per second across all symbols")
    parser.add_argument("--batch-ms", type=int, default=int(os.environ.get("CRIMS_BATCH_MS", "1000")))
    args = parser.parse_args()

    conn_a_m = TickerplantConnection(args.tp_a_m_host, args.tp_a_m_port, "crims_sim.A_M")
    conn_n_z = TickerplantConnection(args.tp_n_z_host, args.tp_n_z_port, "crims_sim.N_Z")
    conn_a_m.connect()
    conn_n_z.connect()

    interval = args.batch_ms / 1000.0
    per_batch = max(1, int(args.rate * interval))

    while True:
        batch_a_m, batch_n_z = [], []
        for _ in range(per_batch):
            sym = random.choice(SYMBOLS_A_M + SYMBOLS_N_Z)
            row = gen_risk(sym)
            (batch_a_m if row[-1] == "A_M" else batch_n_z).append(row)

        if batch_a_m:
            conn_a_m.publish("risk", batch_a_m)
        if batch_n_z:
            conn_n_z.publish("risk", batch_n_z)

        time.sleep(interval)


if __name__ == "__main__":
    main()
