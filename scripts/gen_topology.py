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

# Every kdb service pulls its own binary from the KX portal at container
# start (docker/kdb-entrypoint.sh) using KX_BEARER_TOKEN - no local binary
# folder is mounted anymore (that model only worked on whichever one machine
# a human had manually unzipped a binary onto). KX_CACHE_MOUNT is a
# container-local writable cache of a binary THIS container already pulled
# itself, purely so a restart doesn't re-download - not something anyone is
# expected to populate by hand.
KX_CACHE_MOUNT = "kx-cache:/kdbx-cache"

# KX credentials for the kdb containers - all non-secret DEFAULTS; the real
# values are empty unless set in the environment. KX_BEARER_TOKEN is
# mandatory (see kdb-entrypoint.sh); license is either KDB_LICENSE_B64
# (inline) or KX_LICENSE_PATH (a file mounted some other way - a Kubernetes
# Secret, a secrets-manager sidecar). Neither license form has a local-file
# default anymore, for the same reason there's no local binary folder.
KX_ENV_ANCHOR = """\
x-kdb-env: &kdb-env
  KX_BEARER_TOKEN: "${KX_BEARER_TOKEN:-}"
  KDB_LICENSE_B64: "${KDB_LICENSE_B64:-}"
  KX_VERSION: "${KX_VERSION:-4.1}"
  KX_CHANNEL: "${KX_CHANNEL:-~latest~}"
  KX_ARCH: "${KX_ARCH:-}"
  KX_LICENSE_PATH: "${KX_LICENSE_PATH:-}"
"""


def shards_json(n: int) -> str:
    return json.dumps(topology.shards_json(n), indent=2) + "\n"


def _data_plane_services(n: int, eod_hour: int, idb_retention_days: int, kdb_threads: str,
                         rdb_retention_min: int, hdb_retention_days: int) -> str:
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
      # optional NUMA/CPU pinning via kdb-entrypoint.sh's numactl support -
      # blank (default) = no pinning, unchanged behavior. Operator-set, not
      # auto-derived - see control-api/app/tickhouse.py's HardwareSpec.cpuset
      # docstring for why. TP_CPUSET="0-1" / TP_NUMA_NODE="0" to enable.
      KDB_CPUSET: "${{TP_CPUSET:-}}"
      KDB_NUMA_NODE: "${{TP_NUMA_NODE:-}}"
    restart: unless-stopped
    volumes:
      - {KX_CACHE_MOUNT}
      - tp-log-{sid}:/app/log

  wdb-{sid}:
    build: *kdb-build
    # -retentionmin: how long the chained RDB keeps live data in memory,
    # independent of -flushmin (how often wdb flushes ITS OWN buffer to
    # disk) - see wdb.q's own comment on .wdb.retentionIntv. Must be >=
    # flushmin; wdb.q enforces that itself if misconfigured.
    command: ["wdb.q", "-shard", "{sid}", "-tphost", "tp-{sid}", "-tpport", "{TP}",
              "-flushmin", "2", "-retentionmin", "{rdb_retention_min}",
              "-dbdir", "/data/db", "-hdbdir", "/data/hdb",
              "-idbhost", "idb-{sid}", "-idbport", "{IDB}",
              "-hdbhost", "hdb-{sid}", "-hdbport", "{HDB}", "-p", "{WDB}",
              "-eodhour", "{eod_hour}"]
    environment:
      <<: *kdb-env
      # secondary threads (-s), injected by kdb-entrypoint.sh - see KDB_THREADS
      # there. "auto" sizes it from THIS container's visible CPUs at boot
      # instead of a number baked in at compose-generation time on a
      # possibly different box. wdb has no parallel recovery routine today
      # (unlike rdb's .rdb.loadWarm) so the reserved threads sit idle for
      # now - harmless, and ready if one's added later.
      KDB_THREADS: "${{WDB_THREADS:-{kdb_threads}}}"
    restart: unless-stopped
    depends_on: [tp-{sid}]
    volumes:
      - {KX_CACHE_MOUNT}
      - db-{sid}:/data/db
      - hdb-{sid}:/data/hdb

  rdb-{sid}:
    build: *kdb-build
    command: ["rdb.q", "-shard", "{sid}", "-tphost", "tp-{sid}", "-tpport", "{TP}", "-dbdir", "/data/db", "-p", "{RDB}"]
    environment:
      <<: *kdb-env
      # secondary threads (-s), injected by kdb-entrypoint.sh - see KDB_THREADS
      # there. "auto" sizes it from THIS container's visible CPUs at boot.
      # rdb.q scales active threads back down to 1 once .rdb.loadWarm (the
      # parallel warm-start recovery read) finishes, so the full ceiling is
      # only held for the recovery window, not the process's whole life.
      KDB_THREADS: "${{RDB_THREADS:-{kdb_threads}}}"
      # optional NUMA/CPU pinning - see tp-{sid}'s KDB_CPUSET comment above.
      KDB_CPUSET: "${{RDB_CPUSET:-}}"
      KDB_NUMA_NODE: "${{RDB_NUMA_NODE:-}}"
    restart: unless-stopped
    depends_on: [tp-{sid}]
    volumes:
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
      - {KX_CACHE_MOUNT}
      - db-{sid}:/data/db

  hdb-{sid}:
    build: *kdb-build
    # -retentiondays 0 (default) = keep every sealed day forever - purging
    # HDB history is destructive (no cold-storage archive step, just
    # delete) and opt-in only. See hdb.q's .hdb.purgeOld comment.
    command: ["hdb.q", "-shard", "{sid}", "-hdbdir", "/data/hdb", "-reloadsec", "60",
              "-retentiondays", "{hdb_retention_days}", "-p", "{HDB}"]
    environment:
      <<: *kdb-env
      # see rdb-{sid}'s KDB_THREADS comment above. hdb's periodic reload
      # (system"l dir") is a single built-in mmap call, not peach-driven, but
      # a client SELECT that actually reaches hdb (see control-api's
      # query_router.route_tiers) DOES use these threads automatically - kdb+
      # parallelizes a partitioned-table select across date partitions on its
      # own whenever -s>0, no extra q code needed.
      KDB_THREADS: "${{HDB_THREADS:-{kdb_threads}}}"
      # optional NUMA/CPU pinning - see tp-{sid}'s KDB_CPUSET comment above.
      KDB_CPUSET: "${{HDB_CPUSET:-}}"
      KDB_NUMA_NODE: "${{HDB_NUMA_NODE:-}}"
    restart: unless-stopped
    depends_on: [wdb-{sid}]
    volumes:
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
      # Symbol-group scope (see Connectors page / routers/connectors.py) - the
      # control-api sets this directly on the container when an admin assigns
      # a symbol group, overriding whatever's here; empty = full universe.
      BPIPE_SYMBOLS: "${{BPIPE_SYMBOLS:-}}"
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
      CRIMS_SYMBOLS: "${{CRIMS_SYMBOLS:-}}"   # symbol-group scope, same mechanism as BPIPE_SYMBOLS
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

  # Alpaca live provider (opt-in) - US equities/ETFs, real-time IEX trades
  # free. Same credentials the Bot page's Alpaca paper-trading route uses
  # (ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY) - this container only reads
  # market data with them, never places an order.
  #   docker compose --profile providers up -d alpaca-feed
  alpaca-feed:
    build: *feed-build
    profiles: ["providers"]
    command: ["-m", "providers.runner", "--provider", "alpaca", "--symbols", "${{ALPACA_SYMBOLS:-AAPL,MSFT,GOOGL,AMZN,TSLA}}"]
    environment:
      SHARD_COUNT: "{n}"
      TP_HOST_PATTERN: "tp-{{shard}}"
      TP_PORT: "{TP}"
      ALPACA_API_KEY_ID: "${{ALPACA_API_KEY_ID:-}}"
      ALPACA_API_SECRET_KEY: "${{ALPACA_API_SECRET_KEY:-}}"
      ALPACA_DATA_FEED: "${{ALPACA_DATA_FEED:-iex}}"
      PROVIDER_SYMBOLS_FILE: "${{PROVIDER_SYMBOLS_FILE:-}}"
    volumes:
      - ./data-plane/feeds/symbols:/symbols:ro
    depends_on: [{", ".join(f"tp-{s.id}" for s in topology.shards(n))}]
    restart: on-failure

  # Interactive Brokers live provider (opt-in) - Level 1 quotes via a
  # LOCALLY-RUNNING, already-authenticated Client Portal Gateway (see
  # data-plane/feeds/providers/ibkr.py's docstring - this is NOT a simple
  # API-key integration). IBKR_GATEWAY_BASE_URL's default
  # (https://localhost:5000/v1/api) means "the gateway is reachable from
  # THIS container's own localhost", which is essentially never true in
  # compose - point it at the gateway's real address (e.g.
  # host.docker.internal on Docker Desktop if the gateway runs on the host,
  # or a service name if it's another container on this network, e.g. an
  # IBeam service you add yourself).
  #   docker compose --profile providers up -d ibkr-feed
  ibkr-feed:
    build: *feed-build
    profiles: ["providers"]
    command: ["-m", "providers.runner", "--provider", "ibkr", "--symbols", "${{IBKR_SYMBOLS:-AAPL,MSFT,GOOGL,AMZN,TSLA}}"]
    environment:
      SHARD_COUNT: "{n}"
      TP_HOST_PATTERN: "tp-{{shard}}"
      TP_PORT: "{TP}"
      IBKR_GATEWAY_BASE_URL: "${{IBKR_GATEWAY_BASE_URL:-https://localhost:5000/v1/api}}"
      IBKR_GATEWAY_VERIFY_SSL: "${{IBKR_GATEWAY_VERIFY_SSL:-false}}"
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

  # Coinbase live provider (opt-in) - fully public feed, no API key needed.
  # Symbols are Coinbase's own pair format (BTC-USD, not BTC/USD or BTCUSD).
  #   docker compose --profile providers up -d coinbase-feed
  coinbase-feed:
    build: *feed-build
    profiles: ["providers"]
    command: ["-m", "providers.runner", "--provider", "coinbase", "--symbols", "${{COINBASE_SYMBOLS:-BTC-USD,ETH-USD,SOL-USD}}"]
    environment:
      SHARD_COUNT: "{n}"
      TP_HOST_PATTERN: "tp-{{shard}}"
      TP_PORT: "{TP}"
    depends_on: [{", ".join(f"tp-{s.id}" for s in topology.shards(n))}]
    restart: on-failure

  # Kraken live provider (opt-in) - fully public feed, no API key needed.
  # Symbols are Kraken's own pair format (BTC/USD, not BTC-USD or BTCUSD).
  #   docker compose --profile providers up -d kraken-feed
  kraken-feed:
    build: *feed-build
    profiles: ["providers"]
    command: ["-m", "providers.runner", "--provider", "kraken", "--symbols", "${{KRAKEN_SYMBOLS:-BTC/USD,ETH/USD,SOL/USD}}"]
    environment:
      SHARD_COUNT: "{n}"
      TP_HOST_PATTERN: "tp-{{shard}}"
      TP_PORT: "{TP}"
    depends_on: [{", ".join(f"tp-{s.id}" for s in topology.shards(n))}]
    restart: on-failure

  # Binance live provider (opt-in) - fully public feed, no API key needed.
  # Highest-volume free crypto feed available - symbols are Binance's own
  # concatenated pairs (BTCUSDT, not BTC-USDT or BTC/USDT).
  #   docker compose --profile providers up -d binance-feed
  binance-feed:
    build: *feed-build
    profiles: ["providers"]
    command: ["-m", "providers.runner", "--provider", "binance", "--symbols", "${{BINANCE_SYMBOLS:-BTCUSDT,ETHUSDT,SOLUSDT}}"]
    environment:
      SHARD_COUNT: "{n}"
      TP_HOST_PATTERN: "tp-{{shard}}"
      TP_PORT: "{TP}"
    depends_on: [{", ".join(f"tp-{s.id}" for s in topology.shards(n))}]
    restart: on-failure

  # Binance L2 order-book depth (opt-in) - real bid/ask deltas, far higher
  # message rate per symbol than trade prints. Keep BINANCE_DEPTH_SYMBOLS
  # SHORT (a handful of liquid pairs) - unlike binance-feed above, "all" here
  # would be genuinely excessive load. Rows land in the same `trade` table,
  # tagged venue=binance-depth / side=BID|ASK so they're never confused with
  # real trade prints (see providers/normalize.py's binance_depth).
  #   docker compose --profile providers up -d binance-depth-feed
  binance-depth-feed:
    build: *feed-build
    profiles: ["providers"]
    command: ["-m", "providers.runner", "--provider", "binance-depth", "--symbols", "${{BINANCE_DEPTH_SYMBOLS:-BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT}}"]
    environment:
      SHARD_COUNT: "{n}"
      TP_HOST_PATTERN: "tp-{{shard}}"
      TP_PORT: "{TP}"
    depends_on: [{", ".join(f"tp-{s.id}" for s in topology.shards(n))}]
    restart: on-failure

  # Bybit live provider (opt-in) - fully public feed, no API key needed.
  # Symbols are Bybit's own concatenated pairs (BTCUSDT).
  #   docker compose --profile providers up -d bybit-feed
  bybit-feed:
    build: *feed-build
    profiles: ["providers"]
    command: ["-m", "providers.runner", "--provider", "bybit", "--symbols", "${{BYBIT_SYMBOLS:-BTCUSDT,ETHUSDT,SOLUSDT}}"]
    environment:
      SHARD_COUNT: "{n}"
      TP_HOST_PATTERN: "tp-{{shard}}"
      TP_PORT: "{TP}"
    depends_on: [{", ".join(f"tp-{s.id}" for s in topology.shards(n))}]
    restart: on-failure

  # OKX live provider (opt-in) - fully public feed, no API key needed.
  # Symbols are OKX's own hyphenated pairs (BTC-USDT).
  #   docker compose --profile providers up -d okx-feed
  okx-feed:
    build: *feed-build
    profiles: ["providers"]
    command: ["-m", "providers.runner", "--provider", "okx", "--symbols", "${{OKX_SYMBOLS:-BTC-USDT,ETH-USDT,SOL-USDT}}"]
    environment:
      SHARD_COUNT: "{n}"
      TP_HOST_PATTERN: "tp-{{shard}}"
      TP_PORT: "{TP}"
    depends_on: [{", ".join(f"tp-{s.id}" for s in topology.shards(n))}]
    restart: on-failure

  # Yahoo Finance live provider (opt-in) - no API key, but see
  # providers/yahoo.py's own caveat: unofficial endpoint, delayed/polled
  # quotes, not for production. Widest asset-class spread of any provider
  # here with zero setup: equities, ETFs, indices, and FX in one feed.
  #   docker compose --profile providers up -d yahoo-feed
  yahoo-feed:
    build: *feed-build
    profiles: ["providers"]
    command: ["-m", "providers.runner", "--provider", "yahoo", "--symbols", "${{YAHOO_SYMBOLS:-AAPL,MSFT,SPY,QQQ,^GSPC,^DJI,EURUSD=X,GBPUSD=X,GC=F,CL=F}}"]
    environment:
      SHARD_COUNT: "{n}"
      TP_HOST_PATTERN: "tp-{{shard}}"
      TP_PORT: "{TP}"
      PROVIDER_SYMBOLS_FILE: "${{PROVIDER_SYMBOLS_FILE:-}}"
    volumes:
      - ./data-plane/feeds/symbols:/symbols:ro
    depends_on: [{", ".join(f"tp-{s.id}" for s in topology.shards(n))}]
    restart: on-failure

  # C++ feed-handler engine (opt-in) - protocol/venue adapter platform
  # (data-plane/feedhandler-cpp/), distinct from the Python provider
  # simulators above: real binary-protocol decoders (MoldUDP64/ITCH, SBE,
  # FIX, SoupBinTCP) plus the same WebSocket+JSON crypto-provider shape,
  # activated/configured per FeedHandlerInstance via the admin portal
  # (control-api's app/routers/feedhandlers.py) rather than a CLI flag.
  # FH_MODE=sim (default) publishes synthetic NASDAQ-ITCH-shaped and
  # Coinbase-shaped trades on a repeating interval - no real exchange/
  # vendor connectivity involved - see that directory's own README for
  # FH_MODE=live (a real config JSON + credentials) once you have real
  # entitlements to point it at.
  #   docker compose --profile providers up -d feedhandler-cpp
  feedhandler-cpp:
    build:
      context: ./data-plane/feedhandler-cpp
      dockerfile: docker/Dockerfile
    profiles: ["providers"]
    environment:
      FH_MODE: "${{FEEDHANDLER_MODE:-sim}}"
      FH_KDB_HOST: "tp-s0"
      FH_KDB_PORT: "{TP}"
      FH_SHARD: "s0"
      FH_SIM_INTERVAL_SEC: "${{FEEDHANDLER_SIM_INTERVAL_SEC:-5}}"
      FH_STATUS_PORT: "9200"
      FH_CONFIG_JSON: "${{FEEDHANDLER_CONFIG_JSON:-}}"
    ports:
      - "${{FEEDHANDLER_STATUS_PORT:-9200}}:9200"
    depends_on: [tp-s0]
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
      CLOUD_CREDENTIALS_ENCRYPTION_KEY: "${{CLOUD_CREDENTIALS_ENCRYPTION_KEY:-}}"
      # Reused by app/cloud_provisioner.py to seed the kdbx-license Secret
      # on a newly-terraform-created cluster - the same credentials this
      # control plane's own kdb+ containers already use (see KX_ENV_ANCHOR
      # above), not a separate value to configure twice.
      KX_BEARER_TOKEN: "${{KX_BEARER_TOKEN:-}}"
      KDB_LICENSE_B64: "${{KDB_LICENSE_B64:-}}"
      DATABASE_URL: "${{DATABASE_URL:-sqlite:///./data/control_plane.db}}"
      COMPOSE_PROJECT_NAME: "kdb-control-plane"
      GATEWAY_HOST: gateway
      GATEWAY_PORT: "{GW}"
      NL2Q_LLM_PROVIDER: "${{NL2Q_LLM_PROVIDER:-openai_compatible}}"
      NL2Q_LLM_MODEL: "${{NL2Q_LLM_MODEL:-qwen2.5-coder:3b}}"
      NL2Q_LLM_API_KEY: "${{NL2Q_LLM_API_KEY:-}}"
      NL2Q_LLM_BASE_URL: "${{NL2Q_LLM_BASE_URL:-http://ollama:11434/v1}}"
      NL2Q_LLM_TIMEOUT_SEC: "${{NL2Q_LLM_TIMEOUT_SEC:-60}}"
      S3_BUCKET: "${{S3_BUCKET:-}}"
      S3_REGION: "${{S3_REGION:-}}"
      AZURE_STORAGE_CONNECTION_STRING: "${{AZURE_STORAGE_CONNECTION_STRING:-}}"
      ADLS_ACCOUNT_URL: "${{ADLS_ACCOUNT_URL:-}}"
      ADLS_CONTAINER: "${{ADLS_CONTAINER:-}}"
      EXPORT_BULK_ROW_LIMIT_MAX: "${{EXPORT_BULK_ROW_LIMIT_MAX:-5000000}}"
      EXPORT_GATEWAY_PRESSURE_QUEUE_THRESHOLD: "${{EXPORT_GATEWAY_PRESSURE_QUEUE_THRESHOLD:-200000}}"
      EXPORT_GATEWAY_PRESSURE_LAG_THRESHOLD: "${{EXPORT_GATEWAY_PRESSURE_LAG_THRESHOLD:-1000}}"
      RISK_GATE_FAIL_OPEN: "${{RISK_GATE_FAIL_OPEN:-false}}"
      QUERY_BUDGET_MS_PER_WINDOW: "${{QUERY_BUDGET_MS_PER_WINDOW:-0}}"
      QUERY_BUDGET_WINDOW_HOURS: "${{QUERY_BUDGET_WINDOW_HOURS:-1}}"
      # A product licence key is mandatory for any DEPLOYMENT_ENV other than
      # local/dev - see control-api/app/licensing.py's enforcement_active().
      # Defaults to "local" (unenforced) so a bare `docker compose up` keeps
      # working with zero configuration; the cloud VM deploy scripts set
      # this to "customer" in the .env they generate.
      DEPLOYMENT_ENV: "${{DEPLOYMENT_ENV:-local}}"
      LICENSE_KEY: "${{LICENSE_KEY:-}}"
      LICENSE_ENFORCE: "${{LICENSE_ENFORCE:-}}"
      # Order routing through a real Alpaca account (app/alpaca_broker.py) -
      # blank/off (default) leaves the bot/order ticket on the internal
      # simulated paper fill, unchanged from before this existed. See
      # .env.example's Alpaca block for the full explanation, especially
      # before ever setting ALPACA_TRADING_MODE to anything but "off"/"paper".
      ALPACA_API_KEY_ID: "${{ALPACA_API_KEY_ID:-}}"
      ALPACA_API_SECRET_KEY: "${{ALPACA_API_SECRET_KEY:-}}"
      ALPACA_TRADING_MODE: "${{ALPACA_TRADING_MODE:-off}}"
      ALPACA_LIVE_TRADING_ACK: "${{ALPACA_LIVE_TRADING_ACK:-}}"
      # Order routing through a real Interactive Brokers account
      # (app/ibkr_broker.py) - same off-by-default pattern as Alpaca above.
      # control-api reaches the Gateway itself (order placement), separate
      # from the ibkr-feed container above (market data) - both point at
      # the same gateway but are otherwise independent processes.
      IBKR_GATEWAY_BASE_URL: "${{IBKR_GATEWAY_BASE_URL:-https://localhost:5000/v1/api}}"
      IBKR_GATEWAY_VERIFY_SSL: "${{IBKR_GATEWAY_VERIFY_SSL:-false}}"
      IBKR_TRADING_MODE: "${{IBKR_TRADING_MODE:-off}}"
      IBKR_LIVE_TRADING_ACK: "${{IBKR_LIVE_TRADING_ACK:-}}"
      # Predictive Signals page's news feed (app/news_feed.py) - reuses the
      # SAME Finnhub key the finnhub-feed market-data provider uses (a
      # separate call budget: Finnhub's free tier is a per-minute rate
      # limit, not a shared quota). Alpha Vantage is optional/supplementary
      # (real per-article sentiment scores when configured) - its free tier
      # is heavily request-limited, so blank here just means the news feed
      # runs on Finnhub's own keyword-based sentiment instead, not that it
      # stops working.
      FINNHUB_API_KEY: "${{FINNHUB_API_KEY:-}}"
      ALPHAVANTAGE_API_KEY: "${{ALPHAVANTAGE_API_KEY:-}}"
    volumes:
      - control-api-data:/app/data
      - /var/run/docker.sock:/var/run/docker.sock
    ports:
      - "8000:8000"
    # ollama is intentionally NOT in depends_on even though NL2Q_LLM_BASE_URL
    # points at it by default - control-api never calls it at startup (only
    # lazily, per NL2Q request - see app/main.py's on_startup, which touches
    # neither nl2q nor ollama), so there's no real boot-order requirement,
    # and a hard depends_on would break `docker compose up -d` on a box
    # that's excluded ollama's ["llm"] profile (see below) since Compose
    # refuses to start a service depending on one that's profile-excluded.
    depends_on: [gateway]

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
  #
  # profiles: ["llm"] - opt-in like postgres/providers below, NOT because
  # it's unwanted by default (.env.example ships COMPOSE_PROFILES=llm so a
  # documented `cp .env.example .env` deploy still gets it automatically -
  # same "on by default via .env, opt-out by clearing COMPOSE_PROFILES" as
  # DEPLOYMENT_ENV) but because it costs ~2.4GB RAM held permanently
  # (OLLAMA_KEEP_ALIVE=-1 below) that a free-tier-class cloud VM (~1GB RAM)
  # cannot spare - the deploy/*/04_deploy_stack.sh scripts clear
  # COMPOSE_PROFILES on a detected free-tier box (deploy/lib/free_tier.sh)
  # specifically to exclude this.
  ollama:
    image: ollama/ollama:latest
    restart: unless-stopped
    profiles: ["llm"]
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


def compose(n: int, eod_hour: int = 0, idb_retention_days: int = 5, kdb_threads: str = "auto",
           rdb_retention_min: int = 2, hdb_retention_days: int = 0) -> str:
    header = """\
# GENERATED by scripts/gen_topology.py - do not edit by hand.
# Regenerate for a different shard count with:
#   python scripts/gen_topology.py --shards <N> --compose docker-compose.yml --shards-json data-plane/shards.json
# Add --eod-hour <0-23> / --idb-retention-days <N> to change the trading-day
# rollover hour (UTC, default midnight) or how long idb keeps a sealed day in
# memory before evicting it (default 5), --kdb-threads <N|auto> to change
# the secondary threads (-s) rdb/wdb/hdb reserve (default "auto" - each
# container sizes it from its own visible CPU count at boot via
# kdb-entrypoint.sh; override per-tier at deploy time with the RDB_THREADS /
# WDB_THREADS / HDB_THREADS env vars), --rdb-retention-min <N> to change how
# long the chained RDB keeps live data in memory (default 2, independent of
# the flush cadence - see wdb.q), or --hdb-retention-days <N> to purge
# sealed history older than N days (default 0 = keep forever; destructive
# and opt-in, see hdb.q's .hdb.purgeOld) - all per-TickHouse knobs.
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
        + _data_plane_services(n, eod_hour, idb_retention_days, kdb_threads,
                               rdb_retention_min, hdb_retention_days) + "\n"
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
    p.add_argument("--kdb-threads", default="auto",
                    help="secondary threads (-s) given to rdb/wdb/hdb: an integer, or 'auto' (default) to size "
                         "from each container's own visible CPU count at boot - see kdb-entrypoint.sh")
    p.add_argument("--rdb-retention-min", type=int, default=2,
                    help="minutes of live data the chained RDB keeps in memory (default 2) - independent of "
                         "wdb's own flush cadence, see wdb.q's .wdb.retentionIntv")
    p.add_argument("--hdb-retention-days", type=int, default=0,
                    help="purge sealed HDB history older than N days (default 0 = keep forever - destructive "
                         "and opt-in, see hdb.q's .hdb.purgeOld)")
    p.add_argument("--print", action="store_true", help="print shards.json to stdout")
    args = p.parse_args()

    topology._validate(args.shards)  # fail fast on bad counts

    if args.shards_json:
        with open(args.shards_json, "w") as f:
            f.write(shards_json(args.shards))
        print(f"wrote {args.shards_json} ({args.shards} shards)")
    if args.compose:
        with open(args.compose, "w") as f:
            f.write(compose(args.shards, args.eod_hour, args.idb_retention_days, args.kdb_threads,
                            args.rdb_retention_min, args.hdb_retention_days))
        print(f"wrote {args.compose} ({args.shards} shards)")
    if args.print or not (args.shards_json or args.compose):
        sys.stdout.write(shards_json(args.shards))


if __name__ == "__main__":
    main()
