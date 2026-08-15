# Developer guide

How the codebase is put together, where to make a given kind of change, and
how to run the pieces locally while you work on them. For "what do I need to
prepare before deploying," see `docs/README.md`'s pre-deployment guides
instead — this is about developing the platform itself.

**New to this codebase?** This is reference material, not a tutorial - it
assumes you already have the stack running and know what a tickerplant/
shard/RDB is. If either of those isn't true yet, read
[getting-started.md](getting-started.md) first.

## Repository layout

```
control-api/       FastAPI control plane - auth, tenants, query workspace,
                    trading terminal, fleet, migration/TCO, metrics
data-plane/         the actual q/kdb+ tick chain + feed simulators/providers
  q/                tick.q, rdb.q, wdb.q, idb.q, hdb.q, gateway.q, schema.q
  feeds/            bpipe_sim.py, crims_sim.py, providers/ (real vendors)
  docker/           Dockerfile.kdb, Dockerfile.feed, kdb-entrypoint.sh
web-ui/             React (Vite) frontend
watchdog/           auto-heal daemon - polls container health, runs runbooks
fleet_agent/        remote provisioning agent (runs in a tenant's own cluster)
vscode-extension/   VS Code extension for the query workspace
helm/               Kubernetes chart (pilot/production path)
deploy/             single-VM cloud scripts (AWS/GCP/Azure) + TLS overlays
scripts/            gen_topology.py (generates docker-compose.yml/shards.json),
                    check_topology_sync.py, stage-kdbx.sh
docs/               this guide + the pre-deployment checklists
```

**`docker-compose.yml` is GENERATED — never hand-edit it.** It's rendered by
`scripts/gen_topology.py` from a single shard-count knob (plus a handful of
per-TickHouse flags — thread sizing, retention, EOD hour). Change the
generator, then regenerate:

```bash
python scripts/gen_topology.py --shards 2 --compose docker-compose.yml \
  --shards-json data-plane/shards.json --eod-hour 0 --idb-retention-days 5
```

Three runtimes have to agree on how the symbol space partitions into
shards — `control-api/app/topology.py` (canonical), vendored byte-for-byte
into `watchdog/topology.py` and `data-plane/feeds/topology.py`, plus
`helm/.../templates/_helpers.tpl` (a Sprig reimplementation for the
in-cluster path). `scripts/check_topology_sync.py` checks all three agree —
run it after touching sharding logic:

```bash
python3 scripts/check_topology_sync.py
```

## The tick chain, in one sentence per tier

Per shard: **tp** (tickerplant, receives+relays+logs) → **wdb** (write-down,
buffers + periodically flushes to a scratch file + seals each day into the
hdb at EOD) and **rdb** (chained RDB, warm-starts from wdb's scratch file
then serves live in-memory queries) → **idb** (intraday batch, a short-lived
cache of just-sealed days) and **hdb** (historical, memory-maps sealed
partitions). **gateway** federates across shards for the control plane and
UI. See `docs/tickerplant-administration.md` for the operational detail —
this guide is about *changing* the code, not running it.

## Local development

You don't need q/kdb+ installed locally for control-api or web-ui work —
only the `data-plane` containers need the real binary (see
`data-plane/docker/kdb-entrypoint.sh` — it stages from `KX_BINARIES_DIR` or
pulls from the KX portal with `KX_BEARER_TOKEN`). For q-script changes, you
do need a live stack to test against; there's no local q REPL story here
beyond running the containers.

```bash
cp .env.example .env        # fill in real secrets, see docs/README.md
docker compose up -d --build
```

### control-api

FastAPI + SQLModel, SQLite by default locally (`DATABASE_URL`). Routers live
in `app/routers/`; business logic that isn't trivially inline lives in
top-level `app/*.py` modules (`risk_check.py`, `query_service.py`,
`query_cost.py`, `tickhouse.py`, `oms.py`, ...) so routers stay thin and the
logic is unit-testable without spinning up the whole app.

```bash
cd control-api
pip install -r requirements.txt
python -m pytest tests/ -q          # 300+ tests, no live cluster needed -
                                     # kdb/gateway calls are mocked/injected
```

If you don't have a matching local Python (this repo's `.venv` may be a
stale/mismatched interpreter version — check before trusting it), run tests
in a throwaway container instead:

```bash
docker run --rm -v "$(pwd)":/app -w /app python:3.12-slim \
  sh -c "pip install -q -r requirements.txt pytest && python -m pytest tests/ -q"
```

**Adding an endpoint**: add it to the right router, and if it touches state
worth persisting, add a `SQLModel` to `app/models.py` — tables auto-create
via `SQLModel.metadata.create_all` on startup (`app/db.py`). There's an
`alembic.ini` stub in `control-api/` but no `migrations/` directory and
nothing in the app actually invokes it — in practice, schema changes here
are additive-only (new tables, new nullable/defaulted columns). A real
destructive migration (renaming/dropping a column against a live database)
would need Alembic properly wired up first, not assumed already working.

**Adding a settings knob**: `app/config.py`'s `Settings` dataclass, read
from `os.environ` with a sane default. Wire the env var through
`scripts/gen_topology.py`'s control-api environment block (and `.env` /
`.env.example` if it's something an operator should know exists) so it
survives a `docker-compose.yml` regeneration.

### data-plane (q scripts)

Each tier is one file: `tick.q`, `rdb.q`, `wdb.q`, `idb.q`, `hdb.q`,
`gateway.q`, plus shared `schema.q`. No local q REPL workflow here — the
practical loop is: edit, `docker compose build <service>`, `docker compose
up -d <service>`, watch `docker logs -f <container>`, and query it directly
to verify (`qpython` from inside `control-api`'s container is the easiest
ad hoc client — see the troubleshooting guide for the exact recipe).

**Testing a q change in isolation before it touches live data**: use
`docker compose run --rm -T <service> -q` piped a script via stdin — this
gets you a real q process with the service's real env/volumes but lets you
run arbitrary q without the full startup side effects. Used throughout this
session for testing destructive changes (HDB purge logic) before trusting
them against real data.

### Feed providers (real market data)

`data-plane/feeds/providers/` — one file per vendor, all implementing
`base.MarketDataProvider`. Parsing logic is split into pure functions in
`normalize.py` (unit-tested with sample vendor payloads, no network); the
adapter class wires that into a websocket/polling loop and publishes via the
shared `ShardedPublisher`. To add a provider:

1. Add a `<vendor>_<event>(msg) -> list[Tick]` function to `normalize.py`.
2. Add a `<Vendor>Provider(MarketDataProvider)` class - `name`,
   `display_name`, `live`, `coverage`, `requires`, `WS_URL`, `_handle_raw`,
   `run`. Copy `finnhub.py` or `coinbase.py` as a template.
3. Register it in `providers/__init__.py`'s `_ALL` list.
4. Mirror the catalog entry into `control-api/app/provider_catalog.py`
   (control-api can't import the feeds tree directly, so this is a small,
   deliberately duplicated display-only mirror — keep it in sync).
5. If it should be independently deployable via docker-compose, add a
   `<name>-feed` service block to `scripts/gen_topology.py`'s
   `_feeds_service()`, regenerate `docker-compose.yml`.
6. Tests: `normalize.py` parsing in `providers/tests/test_normalize.py`,
   publish-path + catalog in `providers/tests/test_registry.py`, catalog
   mirror in `control-api/tests/test_connectors_providers.py`.

### web-ui

React 18 + Vite, no TypeScript. `src/api.js` centralizes every HTTP/WS call
to control-api — new pages/components should call through it, not
`fetch` directly. Query workspace uses CodeMirror 6
(`src/components/QueryEditor.jsx` + `src/lib/qLanguage.js`) for editing +
completion; everything else is plain JSX/CSS (`src/styles.css`, one file,
CSS custom properties for the palette).

```bash
cd web-ui
npm install
npm run dev        # Vite dev server, proxies /api to control-api
npm run build       # what actually ships - verify this before declaring
                     # a frontend change done, dev-server-only bugs happen
```

No local Node install on this machine historically during this project's
work — a throwaway `node:20-alpine` container works fine for install/build/
test when needed.

## Testing conventions

- **control-api**: `pytest`, `TestClient(app)` for endpoint tests,
  dependency injection (`connect=` params, `monkeypatch.setattr`) for unit
  tests that need to avoid a live kdb+ connection. Regression tests get a
  comment explaining the real incident they guard against, not just what
  they assert - see `test_query.py`'s `QKeyedTable` test for the pattern.
- **q logic**: no automated test framework - verify destructive/risky
  changes against a real (or disposable, via `docker compose run`) q
  process before trusting them, as this session did for the HDB purge
  logic (constructed fake date-partition directories, verified the exact
  boundary behavior, before ever wiring it against real data).
- **fleet_agent**: `pytest`, pure-function renderers (`tickhouse_render.py`)
  tested directly; the provisioner tested via fake backends.

## Where things commonly go wrong for a first-time contributor

- **Editing `docker-compose.yml` directly** — it's regenerated and your
  edit will be silently lost. Edit `scripts/gen_topology.py`.
- **Assuming `.env`'s defaults are safe to deploy** — `docs/README.md`'s
  shared checklist exists specifically because they aren't (shared secrets,
  demo password hashes).
- **Forgetting the TLS overlay** — if `deploy/tls/docker-compose.local-tls.yml`
  (or the production one) is active, `docker compose up -d <service>` alone
  re-publishes that service's plain-HTTP port directly, which conflicts with
  Caddy already holding it. Always include both `-f` flags together once
  the overlay is in use — see `docs/deployment.md`.
- **Assuming the row-count columns in kdb query results decompose the way
  Python dicts do** — kdb+ result shapes (keyed tables in particular) don't
  map 1:1 onto qpython's own class hierarchy the way you'd guess; see
  `control-api/app/query_service.py`'s `shape_result` and its comments
  before writing new result-shaping code.
