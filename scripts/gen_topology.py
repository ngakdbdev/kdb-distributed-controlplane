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


def shards_json(n: int) -> str:
    return json.dumps(topology.shards_json(n), indent=2) + "\n"


def _data_plane_services(n: int) -> str:
    out = []
    for s in topology.shards(n):
        sid = s.id
        out.append(f"""\
  # ---------------------------------------------------------------- shard {sid} ({s.label})
  tp-{sid}:
    build: *kdb-build
    command: ["tick.q", "{sid}", "-p", "{TP}"]
    restart: unless-stopped
    volumes:
      - tp-log-{sid}:/app/log

  wdb-{sid}:
    build: *kdb-build
    command: ["wdb.q", "-shard", "{sid}", "-tphost", "tp-{sid}", "-tpport", "{TP}",
              "-flushmin", "2", "-dbdir", "/data/db", "-p", "{WDB}"]
    restart: unless-stopped
    depends_on: [tp-{sid}]
    volumes:
      - db-{sid}:/data/db

  rdb-{sid}:
    build: *kdb-build
    command: ["rdb.q", "-shard", "{sid}", "-tphost", "tp-{sid}", "-tpport", "{TP}", "-p", "{RDB}"]
    restart: unless-stopped
    depends_on: [tp-{sid}]

  idb-{sid}:
    build: *kdb-build
    command: ["idb.q", "-shard", "{sid}", "-dbdir", "/data/db", "-pollsec", "15", "-p", "{IDB}"]
    restart: unless-stopped
    depends_on: [wdb-{sid}]
    volumes:
      - db-{sid}:/data/db
""")
    return "\n".join(out)


def _gateway_service(n: int) -> str:
    deps = ", ".join(
        f"{tier}-{s.id}" for s in topology.shards(n) for tier in ("rdb", "idb", "wdb")
    )
    return f"""\
  # -------------------------------------------------------------------- gateway
  # Learns its shard count entirely from the mounted shards.json - never
  # hardcoded. Regenerate shards.json with scripts/gen_topology.py to rescale.
  gateway:
    build: *kdb-build
    command: ["gateway.q", "-p", "{GW}"]
    environment:
      SHARDS_JSON: /app/shards.json
    volumes:
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
    depends_on: [{", ".join(f"tp-{s.id}" for s in topology.shards(n))}]
    # NOT restart:always - toggled on/off by the control API's Connectors screen

  crims-sim:
    build: *feed-build
    command: ["crims_sim.py"]
    environment:
      SHARD_COUNT: "{n}"
      TP_HOST_PATTERN: "tp-{{shard}}"
      TP_PORT: "{TP}"
      CRIMS_RATE_HZ: "${{CRIMS_RATE_HZ:-2}}"
    depends_on: [{", ".join(f"tp-{s.id}" for s in topology.shards(n))}]
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
    volumes:
      - control-api-data:/app/data
      - /var/run/docker.sock:/var/run/docker.sock
    ports:
      - "8000:8000"
    depends_on: [gateway]

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
    vols.append("  control-api-data:")
    vols.append("  postgres-data:")
    return "\n".join(vols)


def compose(n: int) -> str:
    header = """\
# GENERATED by scripts/gen_topology.py - do not edit by hand.
# Regenerate for a different shard count with:
#   python scripts/gen_topology.py --shards <N> --compose docker-compose.yml --shards-json data-plane/shards.json
name: kdb-control-plane

x-kdb-build: &kdb-build
  context: ./data-plane
  dockerfile: docker/Dockerfile.kdb

x-feed-build: &feed-build
  context: ./data-plane
  dockerfile: docker/Dockerfile.feed

services:
"""
    return (
        header
        + _data_plane_services(n) + "\n"
        + _gateway_service(n) + "\n"
        + _feeds_service(n) + "\n"
        + _control_plane_services(n) + "\n"
        + "volumes:\n" + _volumes(n) + "\n"
    )


def main():
    p = argparse.ArgumentParser(description="render shards.json / docker-compose.yml for N shards")
    p.add_argument("--shards", type=int, required=True, help="shard count (1-26)")
    p.add_argument("--shards-json", metavar="PATH", help="write shards.json here")
    p.add_argument("--compose", metavar="PATH", help="write docker-compose.yml here")
    p.add_argument("--print", action="store_true", help="print shards.json to stdout")
    args = p.parse_args()

    topology._validate(args.shards)  # fail fast on bad counts

    if args.shards_json:
        with open(args.shards_json, "w") as f:
            f.write(shards_json(args.shards))
        print(f"wrote {args.shards_json} ({args.shards} shards)")
    if args.compose:
        with open(args.compose, "w") as f:
            f.write(compose(args.shards))
        print(f"wrote {args.compose} ({args.shards} shards)")
    if args.print or not (args.shards_json or args.compose):
        sys.stdout.write(shards_json(args.shards))


if __name__ == "__main__":
    main()
