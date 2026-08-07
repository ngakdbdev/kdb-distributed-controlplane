#!/usr/bin/env python3
"""
gen_topology.py - render the deploy artifacts that can't loop by themselves
from the single shard-count knob.

  python scripts/gen_topology.py --shards 4 --shards-json data-plane/shards.json
  python scripts/gen_topology.py --shards 4 --compose docker-compose.yml

`shards.json` is what the gateway reads to learn its routing table. The Helm
chart generates its own copy in-cluster (templates/_helpers.tpl); this script
is the docker-compose / bare-metal path. Both must agree with app.topology -
scripts/check_topology_sync.py asserts that.

docker-compose can't range over a shard count the way Helm can, so we generate
the whole file here instead of hand-maintaining N copies of each service.
"""
import argparse
import json
import os
import sys

# import the canonical topology module from the control-api tree
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "control-api"))
from app import topology  # noqa: E402

TP = topology.TIER_PORTS["tickerplant"]
RDB = topology.TIER_PORTS["rdb"]
IDB = topology.TIER_PORTS["idb"]
WDB = topology.TIER_PORTS["wdb"]
GW = topology.TIER_PORTS["gateway"]
HDB = topology.TIER_PORTS["hdb"]

# The data folder of KX binaries, mounted read-only into every kdb service. The
# container's entrypoint picks the right <arch>/q at start (docker/kdb-entrypoint.sh),
# so one image runs on amd64 or arm64. Override the host path with KX_BINARIES_DIR.
# The kx-cache volume is writable scratch for the portal-pull fallback.
KX_BIN_MOUNT = "${KX_BINARIES_DIR:-./data-plane/docker/kdbx}:/kdbx:ro"
KX_CACHE_MOUNT = "kx-cache:/kdbx-cache"

# KX binary source for the kdb containers: 'local' uses the staged data folder;
# 'kx-portal' pulls <arch>.zip with the bearer token when it isn't staged. All
# non-secret defaults; the token is empty unless set in the environment.
KX_ENV_ANCHOR = """\
x-kdb-env: &kdb-env
  KX_INSTALL_SOURCE: "${KX_INSTALL_SOURCE:-local}"
  KX_BEARER_TOKEN: "${KX_BEARER_TOKEN:-}"
  KDB_LICENSE_B64: "${KDB_LICENSE_B64:-}"
  KX_VERSION: "${KX_VERSION:-4.1}"
  KX_CHANNEL: "${KX_CHANNEL:-~latest~}"
  KX_ARCH: "${KX_ARCH:-}"
  KX_LICENSE_PATH: "${KX_LICENSE_PATH:-/kdbx/kc.lic}"
"""


def shards_json(n: int) -> str:
    return json.dumps(topology.shards_json(n), indent=2) + "\n"


def _data_plane_services(n: int, eod_hour: int, idb_retention_days: int) -> str:
    out = []
    for s in topology.shards(n):
        sid = s.id
        out.append(f"""\
  # ---------------------------------------------------------------- shard {sid} ({s.label})
  tp-{sid}:
    build: *kdb-build
    command: ["tick.q", "{sid}", "-p", "{TP}", "-eodhour", "{eod_hour}"]
    environment:
      <<: *kdb-env
    restart: unless-stopped
    volumes:
      - {KX_BIN_MOUNT}
      - {KX_CACHE_MOUNT}
      - tp-log-{sid}:/app/log

  wdb-{sid}:
    build: *kdb-build
    command: ["wdb.q", "-shard", "{sid}", "-tphost", "tp-{sid}", "-tpport", "{TP}",
              "-flushmin", "2", "-dbdir", "/data/db", "-hdbdir", "/data/hdb",
              "-idbhost", "idb-{sid}", "-idbport", "{IDB}",
              "-hdbhost", "hdb-{sid}", "-hdbport", "{HDB}", "-p", "{WDB}"]
    environment:
      <<: *kdb-env
    restart: unless-stopped
    depends_on: [tp-{sid}]
    volumes:
      - {KX_BIN_MOUNT}
      - {KX_CACHE_MOUNT}
      - db-{sid}:/data/db
      - hdb-{sid}:/data/hdb

  rdb-{sid}:
    build: *kdb-build
    command: ["rdb.q", "-shard", "{sid}", "-tphost", "tp-{sid}", "-tpport", "{TP}", "-dbdir", "/data/db", "-p", "{RDB}"]
    environment:
      <<: *kdb-env
    restart: unless-stopped
    depends_on: [tp-{sid}]
    volumes:
      - {KX_BIN_MOUNT}
      - {KX_CACHE_MOUNT}
      - db-{sid}:/data/db

  idb-{sid}:
    build: *kdb-build
    command: ["idb.q", "-shard", "{sid}", "-dbdir", "/data/db", "-pollsec", "15",
              "-retentiondays", "{idb_retention_days}", "-p", "{IDB}"]
    environment:
      <<: *kdb-env
    restart: unless-stopped
    depends_on: [wdb-{sid}]
    volumes:
      - {KX_BIN_MOUNT}
      - {KX_CACHE_MOUNT}
      - db-{sid}:/data/db

  hdb-{sid}:
    build: *kdb-build
    command: ["hdb.q", "-shard", "{sid}", "-hdbdir", "/data/hdb", "-reloadsec", "60", "-p", "{HDB}"]
    environment:
      <<: *kdb-env
    restart: unless-stopped
    depends_on: [wdb-{sid}]
    volumes:
      - {KX_BIN_MOUNT}
      - {KX_CACHE_MOUNT}
      - hdb-{sid}:/data/hdb
""")
    return "\n".join(out)


def _gateway_service(n: int) -> str:
    deps = ", ".join(
        f"{tier}-{s.id}" for s in topology.shards(n) for tier in ("rdb", "idb", "wdb", "hdb")
    )
    return f"""\
  # -------------------------------------------------------------------- gateway
  # Learns its shard count entirely from the mounted shards.json - never
  # hardcoded. Regenerate shards.json with scripts/gen_topology.py to rescale.
  gateway:
    build: *kdb-build
    command: ["gateway.q", "-p", "{GW}"]
    environment:
      <<: *kdb-env
      SHARDS_JSON: /app/shards.json
    volumes:
      - {KX_BIN_MOUNT}
      - {KX_CACHE_MOUNT}
      - ./data-plane/shards.json:/app/shards.json:ro
    restart: unless-stopped
    depends_on: [{deps}]
"""


def _feeds_service(n: int) -> str:
    return f"""\
  # ---------------------------------------------------------------- feed sims
  # Fan out across all {n} tickerplants via SHARD_COUNT + TP_HOST_PATTERN.
  bpipe-sim:
    build: *feed-build
    command: ["bpipe_sim.py"]
    environment:
      SHARD_COUNT: "{n}"
      TP_HOST_PATTERN: "tp-{{shard}}"
      TP_PORT: "{TP}"
      BPIPE_RATE_HZ: "${{BPIPE_RATE_HZ:-20}}"
      SIM_SYMBOL_COUNT: "${{SIM_SYMBOL_COUNT:-0}}"   # grow the universe (e.g. 1000)
      BPIPE_SYMBOLS_FILE: "${{BPIPE_SYMBOLS_FILE:-}}"
    volumes:
      - ./data-plane/feeds/symbols:/symbols:ro
    depends_on: [{", ".join(f"tp-{s.id}" for s in topology.shards(n))}]
    # on-failure (not always): recovers from a crash / a not-yet-ready TP, but a
    # clean stop from the Connectors screen stays stopped.
    restart: on-failure

  crims-sim:
    build: *feed-build
    command: ["crims_sim.py"]
    environment:
      SHARD_COUNT: "{n}"
      TP_HOST_PATTERN: "tp-{{shard}}"
      TP_PORT: "{TP}"
      CRIMS_RATE_HZ: "${{CRIMS_RATE_HZ:-2}}"
    depends_on: [{", ".join(f"tp-{s.id}" for s in topology.shards(n))}]
    restart: on-failure

  # Real market-data provider (opt-in). Streams live ticks into the same
  # tickerplants instead of the synthetic sims. Needs an API key supplied via
  # .env - never hardcode it here. Start with:
  #   docker compose --profile providers up -d finnhub-feed
  finnhub-feed:
    build: *feed-build
    profiles: ["providers"]
    command: ["-m", "providers.runner", "--provider", "finnhub", "--symbols", "${{FINNHUB_SYMBOLS:-AAPL,MSFT,GOOGL,AMZN,TSLA}}"]
    environment:
      SHARD_COUNT: "{n}"
      TP_HOST_PATTERN: "tp-{{shard}}"
      TP_PORT: "{TP}"
      FINNHUB_API_KEY: "${{FINNHUB_API_KEY:-}}"
      PROVIDER_SYMBOLS_FILE: "${{PROVIDER_SYMBOLS_FILE:-}}"
    volumes:
      - ./data-plane/feeds/symbols:/symbols:ro
    depends_on: [{", ".join(f"tp-{s.id}" for s in topology.shards(n))}]
    restart: on-failure

  # Twelve Data live provider (opt-in). Key from .env only - never hardcode.
  #   docker compose --profile providers up -d twelvedata-feed
  twelvedata-feed:
    build: *feed-build
    profiles: ["providers"]
    command: ["-m", "providers.runner", "--provider", "twelvedata", "--symbols", "${{TWELVEDATA_SYMBOLS:-AAPL,MSFT,GOOGL,AMZN,TSLA}}"]
    environment:
      SHARD_COUNT: "{n}"
      TP_HOST_PATTERN: "tp-{{shard}}"
      TP_PORT: "{TP}"
      TWELVEDATA_API_KEY: "${{TWELVEDATA_API_KEY:-}}"
      PROVIDER_SYMBOLS_FILE: "${{PROVIDER_SYMBOLS_FILE:-}}"
    volumes:
      - ./data-plane/feeds/symbols:/symbols:ro
    depends_on: [{", ".join(f"tp-{s.id}" for s in topology.shards(n))}]
    restart: on-failure
"""


def _control_plane_services(n: int) -> str:
    return f"""\
  # ------------------------------------------------------------------ control
  # Optional local Postgres for testing the real-database path (see
  # control-api/README-database.md). Opt in with:
  #   docker compose --profile postgres up -d postgres
  postgres:
    image: postgres:16
    profiles: ["postgres"]
    restart: unless-stopped
    environment:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: kdb_control_plane
    volumes:
      - postgres-data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  control-api:
    build: ./control-api
    restart: unless-stopped
    environment:
      SHARD_COUNT: "{n}"
      PLATFORM_ADMIN_EMAIL: "${{PLATFORM_ADMIN_EMAIL:-admin@platform.local}}"
      PLATFORM_ADMIN_PASSWORD_HASH: "${{PLATFORM_ADMIN_PASSWORD_HASH:-}}"
      JWT_SECRET: "${{JWT_SECRET:-dev-secret-change-in-deploy}}"
      WATCHDOG_SHARED_SECRET: "${{WATCHDOG_SHARED_SECRET:-dev-watchdog-secret-change-in-deploy}}"
      DATABASE_URL: "${{DATABASE_URL:-sqlite:///./data/control_plane.db}}"
      COMPOSE_PROJECT_NAME: "kdb-control-plane"
      GATEWAY_HOST: gateway
      GATEWAY_PORT: "{GW}"
      NL2Q_LLM_PROVIDER: "${{NL2Q_LLM_PROVIDER:-openai_compatible}}"
      NL2Q_LLM_MODEL: "${{NL2Q_LLM_MODEL:-qwen2.5-coder:3b}}"
      NL2Q_LLM_API_KEY: "${{NL2Q_LLM_API_KEY:-}}"
      NL2Q_LLM_BASE_URL: "${{NL2Q_LLM_BASE_URL:-http://ollama:11434/v1}}"
      NL2Q_LLM_TIMEOUT_SEC: "${{NL2Q_LLM_TIMEOUT_SEC:-60}}"
    volumes:
      - control-api-data:/app/data
      - /var/run/docker.sock:/var/run/docker.sock
    ports:
      - "8000:8000"
    depends_on: [gateway, ollama]

  # Open-weights model server for the query workspace's natural-language-to-q
  # box (control-api/app/nl2q.py) - the default NL2Q_LLM_* above point here.
  # Runs on CPU inside Docker Desktop (no Metal passthrough for Linux
  # containers on macOS) - fine for an occasional "generate q" click, not a
  # chat interface. Not published to the host; only control-api needs it,
  # over the compose network at http://ollama:11434.
  # First boot: pull the default model once with
  #   docker exec kdb-control-plane-ollama-1 ollama pull qwen2.5-coder:3b
  # On a beefier / GPU host, bump NL2Q_LLM_MODEL to qwen2.5-coder:7b or
  # :32b for materially better query generation (pull that tag instead).
  ollama:
    image: ollama/ollama:latest
    restart: unless-stopped
    environment:
      # Default keep_alive is 5 minutes of inactivity before Ollama unloads
      # the model - confirmed empirically to cost ~9s to reload from disk on
      # the next request (vs ~0.3s once warm). "-1" keeps it resident
      # indefinitely once first loaded; costs ~2.4GB RAM permanently, which
      # this box's Docker VM has headroom for.
      OLLAMA_KEEP_ALIVE: "-1"
    volumes:
      - ollama-data:/root/.ollama

  watchdog:
    build: ./watchdog
    restart: unless-stopped
    environment:
      SHARD_COUNT: "{n}"
      COMPOSE_PROJECT_NAME: "kdb-control-plane"
      CONTROL_API_URL: "http://control-api:8000"
      WATCHDOG_SHARED_SECRET: "${{WATCHDOG_SHARED_SECRET:-dev-watchdog-secret-change-in-deploy}}"
      WATCHDOG_POLL_SEC: "5"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    depends_on: [control-api]

  web-ui:
    build: ./web-ui
    restart: unless-stopped
    ports:
      - "80:80"
    depends_on: [control-api]
"""


def _volumes(n: int) -> str:
    vols = []
    for s in topology.shards(n):
        vols.append(f"  tp-log-{s.id}:")
        vols.append(f"  db-{s.id}:")
        vols.append(f"  hdb-{s.id}:")
    vols.append("  control-api-data:")
    vols.append("  postgres-data:")
    vols.append("  ollama-data:")
    return "\n".join(vols)


def compose(n: int, eod_hour: int = 0, idb_retention_days: int = 5) -> str:
    header = """\
# GENERATED by scripts/gen_topology.py - do not edit by hand.
# Regenerate for a different shard count with:
#   python scripts/gen_topology.py --shards <N> --compose docker-compose.yml --shards-json data-plane/shards.json
# Add --eod-hour <0-23> / --idb-retention-days <N> to change the trading-day
# rollover hour (UTC, default midnight) or how long idb keeps a sealed day in
# memory before evicting it (default 5) - both per-TickHouse knobs.
name: kdb-control-plane

x-kdb-build: &kdb-build
  context: ./data-plane
  dockerfile: docker/Dockerfile.kdb

x-feed-build: &feed-build
  context: ./data-plane
  dockerfile: docker/Dockerfile.feed

""" + KX_ENV_ANCHOR + """
services:
"""
    return (
        header
        + _data_plane_services(n, eod_hour, idb_retention_days) + "\n"
        + _gateway_service(n) + "\n"
        + _feeds_service(n) + "\n"
        + _control_plane_services(n) + "\n"
        + "volumes:\n" + _volumes(n) + "\n  kx-cache:\n"
    )


def main():
    p = argparse.ArgumentParser(description="render shards.json / docker-compose.yml for N shards")
    p.add_argument("--shards", type=int, required=True, help="shard count (1-26)")
    p.add_argument("--shards-json", metavar="PATH", help="write shards.json here")
    p.add_argument("--compose", metavar="PATH", help="write docker-compose.yml here")
    p.add_argument("--eod-hour", type=int, default=0,
                    help="UTC hour the trading day rolls over at (0-23, default 0 = midnight UTC)")
    p.add_argument("--idb-retention-days", type=int, default=5,
                    help="days idb keeps a sealed day in memory before evicting it (default 5)")
    p.add_argument("--print", action="store_true", help="print shards.json to stdout")
    args = p.parse_args()

    topology._validate(args.shards)  # fail fast on bad counts

    if args.shards_json:
        with open(args.shards_json, "w") as f:
            f.write(shards_json(args.shards))
        print(f"wrote {args.shards_json} ({args.shards} shards)")
    if args.compose:
        with open(args.compose, "w") as f:
            f.write(compose(args.shards, args.eod_hour, args.idb_retention_days))
        print(f"wrote {args.compose} ({args.shards} shards)")
    if args.print or not (args.shards_json or args.compose):
        sys.stdout.write(shards_json(args.shards))


if __name__ == "__main__":
    main()
