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
deploy/gcp|aws|azure/ per-cloud provisioning, networking, docker install, deploy, and teardown scripts
reference/              KX's own public tick.q reference (submodule, read-only, not a build dependency - see reference/README.md)
docker-compose.yml     Ties the whole stack together
.env.example           Copy to .env and fill in before deploying anywhere but your laptop
```

## Running locally

1. Download KDB-X Community Edition (free) from the KX Developer Center and place the `q` binary and
   `k4.lic` license at `data-plane/docker/kdbx/`.
2. `cp .env.example .env` and fill in real secrets.
3. `docker compose build && docker compose up -d`
4. Open `http://localhost/` and log in. Two accounts are seeded on first boot:
   the platform admin (`PLATFORM_ADMIN_EMAIL`, default `admin@platform.local`) for
   fleet/tenant management, and a demo tenant admin (`DEMO_TENANT_ADMIN_EMAIL`,
   default `admin@demo-bank.local`) for the tenant-scoped screens. Passwords come
   from the matching `*_PASSWORD_HASH` env vars (`changeme` if left blank, local-only).
   Microsoft Entra (OIDC) SSO and on-prem LDAP/AD are also wired per tenant — see
   the auth routers.
5. Enable the `bpipe-sim` and `crims-sim` connectors from the Connectors tab.
6. Watch the Metrics tab fill in, and try killing a process from the Topology tab to see the watchdog
   heal it - check the Audit log tab afterward.

## Demo & load-test

`demokit/` is the sales toolkit. `python -m demokit.demo` runs the whole
walkthrough for you — log in, enable feeds, kill a tickerplant, watch the
watchdog heal it, read the audit trail — and doubles as a stack smoke test.
`python -m demokit.load_test` measures throughput (offered vs achieved ingest,
and where it starts shedding) and demonstrates slow-subscriber auto-discard
with real numbers. See `demokit/README.md` for flags and `DEMO.md` for the
presenter script. The measurement core is unit-tested (`pytest demokit`) so the
numbers are honest even though they can only be produced on a live deployment.

## Deploying to the cloud (GCP / AWS / Azure) or Kubernetes

Each cloud has a parallel module under `deploy/` with the same five steps
(provision → networking → docker → deploy → teardown) and its own README:

- `deploy/gcp/` — C3 + Tier_1 networking + compact placement.
- `deploy/aws/` — C7i + cluster placement + ENA, with an **opt-in, off-by-default
  F2 FPGA** path (`ENABLE_FPGA=1`) that only provisions the FPGA-capable box.
- `deploy/azure/` — accelerated networking + proximity placement; **no FPGA
  path**, because Azure's NP FPGA family is being retired (May 2027).

For Kubernetes (EKS/AKS/GKE/on-prem), see `helm/kdb-control-plane/` — a matching set of
`values-aws.yaml` / `values-azure.yaml` / `values-gcp.yaml` overlays live alongside the chart.

**Before running any of the above**, work through `docs/README.md` — a pre-deployment checklist
(IAM/RBAC least-privilege policies, the secret-rotation checklist, KX licensing paths, database
decisions, DNS/TLS) with one detailed guide per target: `docs/predeploy-aws.md`,
`docs/predeploy-gcp.md`, `docs/predeploy-azure.md`, `docs/predeploy-kubernetes.md`.

On FPGA generally: no cloud FPGA instance accelerates kdb+ out of the box. The
FPGA is inert until you build and load a custom bitstream (AWS AFI / Azure
attested Vitis image) and wire a feed handler to it — kdb+ never runs on the
FPGA. Each README says this plainly so you can too. Real FPGA feed-handling in
finance is overwhelmingly on-prem with kernel-bypass NICs; treat cloud FPGA as
"we can host your accelerator design," not "kdb+ is now FPGA-accelerated."

## What's built (and what's still honest to caveat)

Delivered since the first cut:

- **N-way sharding is real**, not a hardcoded A-M/N-Z split. `SHARD_COUNT` is a
  single knob; the q processes, gateway routing, feed fan-out, watchdog targets,
  and Helm PVCs all derive from it (`scripts/gen_topology.py`, `topology.py`,
  guarded against drift by `scripts/check_topology_sync.py`).
- **Multi-runbook, signature-based self-healing.** The watchdog classifies each
  failure (container down / unhealthy / dependency-down / flapping) and dispatches
  the right runbook — restart-and-verify, defer-to-dependency (heal a tickerplant
  before its dependent RDB/IDB), or escalate-and-back-off when a service is
  flapping. Not one blind restart loop.
- **Multi-tenant auth.** Per-tenant Microsoft Entra (OIDC/PKCE) SSO, on-prem
  LDAP/AD bind, and local accounts; `platform_admin` is never grantable via tenant
  federation.
- **Slow-subscriber auto-discard** in `tick.q` (strike-based, over a byte
  threshold), surfaced in the Audit tab and demonstrable via `demokit`.
- **A real load-test harness** (`demokit/`) with a unit-tested measurement core.

Still caveat these to a prospect — don't let them assume otherwise:

- Feed simulators are synthetic, not live vendor connections.
- Throughput is hardware/shard/version dependent. Quote only numbers you
  measured with `demokit.load_test` on the target spec — never a figure carried
  between demos.
- The `/topology` router drives the orchestrator directly, which is correct for a
  single-tenant dedicated deployment (one bank, in their own cluster). The hosted
  multi-tenant path uses `/fleet` agents instead — see the note in
  `routers/topology.py`.
- `gateway.q` and the Helm chart render need a real KDB-X and a `helm` binary to
  verify end-to-end; they're covered by tests where the logic is Python, but run
  the stack and `helm template` on a real box before trusting them in front of a
  client.
