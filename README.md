# TickForge — a kdb+ tick control plane

A control plane (web UI + API + self-healing watchdog) sitting above a sharded kdb+ tick deployment
built on the Tick-X pattern (tickerplant → write-down DB → chained RDB → intraday DB, split across N
symbol-range shards). Started as a two-week demo to show prospective clients a working self-healing,
observable, sharded tick architecture, and has since grown a full operations surface on top of that:
live query with real autocomplete, bulk data export, client-computed autoscaling recommendations, and
a paper-trading terminal exercising the same query path. Still **not** a production multi-tenant
platform on its own — see "What's still honest to caveat" below before you point it at anything real.

Product name in the UI is **TickForge** (Qbyte Computing Limited); the codebase and container names
still say `kdb-control-plane` throughout, unchanged.

## Licensing note - the kdb+/KDB-X engine is never bundled here

The `q` binary and license file are proprietary (even the free KDB-X Community Edition requires
its own license terms and is not redistributable). This repo never contains them - `.gitignore`
excludes `data-plane/docker/kdbx/q` and `k4.lic` on purpose. Download KDB-X yourself from the KX
Developer Center and place both files at `data-plane/docker/kdbx/` before building the data-plane
images. `reference/` contains KX's own public *scripts* (not the engine) as read-only reference
material - see `reference/README.md` for exactly what that is and isn't.

## What's real vs. simulated

- **Real**: the q/kdb+ tick architecture, the control API, the watchdog's self-healing runbooks, the
  web UI, the metrics/audit pipeline, the query workspace (including its autocomplete and Parquet/S3/
  ADLS export), and the autoscaling recommendation engine. All of this has been run and tested, not
  just written.
- **Simulated**: the market data itself. `bpipe_sim.py` and `crims_sim.py` generate synthetic data
  shaped like Bloomberg B-PIPE and a CRIMS-style risk feed - they are not real vendor connectors and
  use no vendor SDKs or credentials. Real (delayed/free-tier) providers are also wired in as an
  opt-in path (Finnhub, Twelvedata - see Connectors below) for when synthetic data isn't enough.
- **Paper only, always**: the trading terminal (Markets/Orders/Portfolio/Bot) never routes anywhere
  real. `oms.py`'s `BrokerRouter` seam is defined but deliberately unimplemented - see the Trading
  terminal section below for why that's a hard line, not a missing feature.

## Repository layout

```
data-plane/           q/kdb+ processes (tickerplant, wdb, rdb, idb, hdb, gateway) + feed simulators
                       + export/ (Parquet/S3/ADLS/Snowflake/Databricks/Fabric sink framework)
control-api/          FastAPI control plane: auth, topology, metrics, query, connectors, subscribers,
                       trading (paper), audit, background export jobs
watchdog/              Self-healing service - deterministic runbooks, independent of the control API
web-ui/                React dashboard (Vite), dark Trading-212-inspired design system
docs/                  Developer/usage/admin/troubleshooting/deployment guides, plus
                       pre-deployment checklists (one per target) - read before deploy/ or helm/
deploy/gcp|aws|azure/ per-cloud provisioning, networking, docker install, deploy, and teardown scripts
helm/kdb-control-plane/ Kubernetes chart + per-cloud values-aws/azure/gcp.yaml overlays
reference/              KX's own public tick.q reference (submodule, read-only, not a build dependency - see reference/README.md)
docker-compose.yml     Ties the whole stack together (GENERATED - see scripts/gen_topology.py)
.env.example           Copy to .env and fill in before deploying anywhere but your laptop
```

## Running locally

1. Download KDB-X Community Edition (free) from the KX Developer Center and place the `q` binary and
   `k4.lic` license at `data-plane/docker/kdbx/`.
2. `cp .env.example .env` and fill in real secrets (see `docs/README.md`'s secret-rotation checklist -
   don't ship what's in `.env.example` verbatim; some values there look real, not placeholder).
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
7. Explore the rest: **Query** (write q with schema-aware autocomplete, or describe it in English;
   download results as Parquet, or export bulk pulls to S3/ADLS in the background), **Markets /
   Orders / Portfolio / Bot** (the paper trading terminal), and **Autoscaling** (a live shard-scaling
   recommendation with real per-shard sync status).

## Web UI

A dark, card-based design system (deliberately inspired by consumer trading-app UI conventions -
bold tabular-number hero stats, sparklines, restrained green/red status color - adapted to this
product's actual domain of infra operations, not personal trading). Pages, grouped as in the sidebar:

- **Overview / Topology** - the operations home screen and the live process-control view (start/stop/
  restart/kill any container; kill one to see the watchdog heal it).
- **Live monitoring** - Tickerplants (per-TP deep dive over raw IPC), Metrics (streaming throughput,
  transit lag, latency distribution), Alerts (client-synthesized from live metrics/market/execution
  signals, no separate alert store).
- **Query** - the live query workspace: q with autocomplete (see below), plain-English → q, plain-
  English → a q function, a deterministic + optionally LLM-backed query advisor, and result export.
- **Trading** (paper only) - **Markets** (per-symbol watchlist, candlestick drilldown, tape/depth, a
  calendar-horizon forecast), **Orders** (the order ticket + option greeks + blotter), **Portfolio**
  (positions, a 10-minute exposure forecast per holding, basket correlation), **Bot** (a risk-capped
  paper trading bot), **Execution** (fill-rate/execution-quality analytics).
- **Data** - Connectors (feed sims + real opt-in providers), Subscribers (entitlements), Data export
  (the offline sink catalog / CLI pointer).
- **Manage** - TickHouses (the declarative cluster wizard), **Autoscaling** (see below), Fleet (agent
  registration for the hosted multi-tenant path), Audit log (every admin action + every watchdog
  auto-heal, as an activity feed).
- **Sales** - Migration assessment (static `.q` script analysis + TCO estimate, nothing persisted).
- **Admin** (platform admin only) - Model settings (which LLM backs NL2Q/codegen/analysis).

### Query workspace autocomplete

Full q/kdb+ built-in vocabulary (~150 keywords/functions, not a hand-picked shortlist), plus **live**
schema awareness: column names come from an actual `cols <table>` read against a real RDB target (not
a hardcoded list), so it's correct for any table this deployment has, not just `trade`/`risk`. Ranking
is context-aware - table names are prioritized right after `from`, column names right after `where`/
`by`/`select`/`update`/`exec` - and the dropdown supports arrow-key navigation, not just "accept the
first suggestion." Generated code (from the plain-English boxes) lands in the same editor, so it gets
the same autocomplete on the next edit.

### Query result export

Three ways to get data out of a query result, from the same Query page:
- **Download Parquet** (local) - exports exactly the grid on screen as a real `.parquet` file
  (pyarrow-built, type-inferred from the live result). Capped at 10GB.
- **Background export to S3 or ADLS** - for pulls too large for that cap. Re-runs the query
  server-side against a much higher row ceiling than the interactive grid uses
  (`EXPORT_BULK_ROW_LIMIT_MAX`), reports real upload progress (actual transferred bytes from
  boto3/azure-storage-blob, not an estimate), and checks live gateway load first - if it's elevated,
  the job flags it and points at the Autoscaling page rather than silently making the load worse or
  triggering a scaling action on its own. Needs S3/ADLS credentials configured server-side (see
  `.env.example`) - never entered in the UI.
- `data-plane/export/` also has a standalone CLI (`python -m export.runner`) with the same sink
  framework (parquet/s3/adls/snowflake/databricks/fabric) for HDB-history bulk pulls outside the UI.

### Autoscaling

A shard-scaling recommendation computed **entirely client-side** from real live metrics (same
`/metrics/snapshot` and `/topology/status` data Overview/Metrics already poll - same pattern Alerts
already uses for its own client-synthesized signals, so this added zero backend surface). Shows a
"Shard sync status" per shard as four real, observed milestones (Provisioning → Connecting →
Catching up → Live) - not a fabricated percentage, because there genuinely isn't one: `rdb.q` has no
tickerplant-log replay, a restarted RDB just resubscribes and waits for live ticks, so the only real
"catching up" signal is RDB staleness lag, extrapolated to an ETA the same way the self-healing
recovery toasts already do. **Apply** wires to the real, already-existing fleet-provisioning API
(the same one the Fleet page uses); auto-apply defaults off, since a shard-count change is a real
topology change (new shards start empty, nothing rebalances existing data).

### Trading terminal (Markets / Orders / Portfolio / Bot) - why it's paper-only, hard line

This exercises the same live query path as everything else with realistic-looking market UI, which is
useful for a demo - but it will **never** route anywhere real from this repo. The market data
underneath it is synthetic (see above), so executing real money against fake prices would be actively
harmful, and real broker/bank integration means real regulatory obligations (broker-dealer
registration, KYC/AML, payment-services authorization) a demo control plane has no business taking on
by accident. `oms.py` ships a `PaperRouter` only; its `BrokerRouter` seam is defined and documented but
deliberately raises rather than connecting anywhere. The **Bot** page's "paper capital" is a number you
set yourself, stored in your browser only - never a real bank account - and its position sizing is
hard-capped at 1% risk of that number regardless of what's typed into the field.

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
`docs/predeploy-gcp.md`, `docs/predeploy-azure.md`, `docs/predeploy-kubernetes.md`. For the actual
step-by-step commands (and what "done" looks like) once you're past that checklist, see
`docs/deployment-process.md`. Operating the platform day to day: `docs/tickerplant-administration.md`
and `docs/troubleshooting.md`. Extending the codebase: `docs/developer-guide.md`. What every UI page
actually does: `docs/platform-usage.md`.

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
- **Schema-aware query autocomplete**, live against the real cluster, not a
  static keyword list (see "Query workspace autocomplete" above).
- **Bulk query export** to Parquet (local, 10GB cap) or S3/ADLS (background,
  real upload progress, a live gateway-pressure check first).
- **A client-computed autoscaling recommendation** with an honest, milestone-based
  (not fabricated-percentage) shard sync status, wired to the real fleet-provisioning
  Apply path.
- **A calendar-horizon market forecast** (10m/15m/30m/1h, from real per-minute
  drift/volatility over actual trade timestamps) replacing an earlier "next N
  ticks" projection that had no real time semantics.

Still caveat these to a prospect — don't let them assume otherwise:

- Feed simulators are synthetic, not live vendor connections. Finnhub/Twelvedata
  are real but opt-in, delayed/free-tier, and need your own API keys.
- Throughput is hardware/shard/version dependent. Quote only numbers you
  measured with `demokit.load_test` on the target spec — never a figure carried
  between demos.
- The trading terminal is paper-only, permanently — see above for why that's a
  design decision, not a gap to fill in later.
- Background export to S3/ADLS needs real cloud credentials configured
  server-side; without them it fails with a clear error rather than a fake
  success (this is by design - `SinkNotConfigured`, never a pretended write).
- Autoscaling's Apply needs at least one environment/agent registered on the
  Fleet page; the recommendation itself is still real and live without one, it
  just has nowhere to send the command.
- The `/topology` router drives the orchestrator directly, which is correct for a
  single-tenant dedicated deployment (one bank, in their own cluster). The hosted
  multi-tenant path uses `/fleet` agents instead — see the note in
  `routers/topology.py`.
- `gateway.q` and the Helm chart render need a real KDB-X and a `helm` binary to
  verify end-to-end; they're covered by tests where the logic is Python, but run
  the stack and `helm template` on a real box before trusting them in front of a
  client.
