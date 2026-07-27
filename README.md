# kdb+ tick control plane - demo MVP

A control plane (web UI + API + self-healing watchdog) sitting above a sharded kdb+ tick deployment
built on the Tick-X pattern (tickerplant → write-down DB → chained RDB → intraday DB, split across two
symbol-range shards). Built as a two-week demo to show prospective clients a working self-healing,
observable, sharded tick architecture - **not** a production multi-tenant platform. See the plan
document (`kdb-control-plane-mvp-plan.md`, shared alongside this project) for full scope notes.

## Licensing note - the kdb+/KDB-X engine is never bundled here

The `q` binary and license file are proprietary (even the free KDB-X Community Edition requires
its own license terms and is not redistributable). This repo never contains them - `.gitignore`
excludes `data-plane/docker/kdbx/q` and `k4.lic` on purpose. Download KDB-X yourself from the KX
Developer Center and place both files at `data-plane/docker/kdbx/` before building the data-plane
images. `reference/` contains KX's own public *scripts* (not the engine) as read-only reference
material - see `reference/README.md` for exactly what that is and isn't.

## What's real vs. simulated

- **Real**: the q/kdb+ tick architecture, the control API, the watchdog's self-healing runbooks, the
  web UI, the metrics/audit pipeline. All of this has been run and tested, not just written.
- **Simulated**: the market data itself. `bpipe_sim.py` and `crims_sim.py` generate synthetic data
  shaped like Bloomberg B-PIPE and a CRIMS-style risk feed - they are not real vendor connectors and
  use no vendor SDKs or credentials. They demonstrate the connector pattern a real feed handler would
  slot into.

## Repository layout

```
data-plane/           q/kdb+ processes (tickerplant, wdb, rdb, idb, gateway) + feed simulators
control-api/          FastAPI control plane: auth, topology, metrics, connectors, subscribers, audit
watchdog/              Self-healing service - deterministic runbooks, independent of the control API
web-ui/                React dashboard (Vite + recharts)
deploy/gcp/            GCP provisioning, networking, docker install, deploy, and teardown scripts
reference/              KX's own public tick.q reference (submodule, read-only, not a build dependency - see reference/README.md)
docker-compose.yml     Ties the whole stack together
.env.example           Copy to .env and fill in before deploying anywhere but your laptop
```

## Running locally

1. Download KDB-X Community Edition (free) from the KX Developer Center and place the `q` binary and
   `k4.lic` license at `data-plane/docker/kdbx/`.
2. `cp .env.example .env` and fill in real secrets.
3. `docker compose build && docker compose up -d`
4. Open `http://localhost/` - log in with `admin` / the password behind whatever `ADMIN_PASSWORD_HASH`
   you set (or `changeme` if you left it blank, local-only).
5. Enable the `bpipe-sim` and `crims-sim` connectors from the Connectors tab.
6. Watch the Metrics tab fill in, and try killing a process from the Topology tab to see the watchdog
   heal it - check the Audit log tab afterward.

## Deploying to GCP

See `deploy/gcp/README.md` for the full walkthrough, including the honest explanation of why this uses
Tier_1 networking + compact placement + C3 machines instead of FPGA (GCP has no FPGA instance family).

## Known limitations (say these out loud to a prospect, don't let them assume otherwise)

- Single-VM deployment - no real horizontal auto-scaling demoed yet.
- Basic single-admin auth, not multi-tenant IAM.
- Feed simulators are synthetic, not live vendor connections.
- The watchdog has exactly one runbook (restart + verify). A production system would have several,
  matched to specific failure signatures.
- No load testing performed yet - don't quote a throughput number you haven't personally measured
  with this exact deployment.
