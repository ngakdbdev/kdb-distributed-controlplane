# Platform usage guide

What the web UI actually does, page by page. For how the underlying tick
chain works, see `docs/tickerplant-administration.md`. For what to do when
something's broken, see `docs/troubleshooting.md`.

**First time here?** This assumes the stack is already running and you've
logged in once. If you haven't gotten that far yet, or terms like
"tickerplant" or "shard" are unfamiliar, start with
[getting-started.md](getting-started.md) instead - it walks through both.

## Logging in

`/login` — tenant users authenticate against the platform's own credential
store, LDAP (if the tenant has one configured), or SSO (Microsoft Entra, if
configured). The seeded local-demo tenant admin is
`admin@demo-bank.local` / `changeme` unless overridden via
`DEMO_TENANT_ADMIN_EMAIL`/`DEMO_TENANT_ADMIN_PASSWORD_HASH`. A separate
platform-admin account (`PLATFORM_ADMIN_EMAIL`, default
`admin@platform.local`) operates across every tenant, not scoped to one.

## Overview

The landing page. A live-updating (1s, over a WebSocket) summary: cluster
health (every managed process, green/red), throughput and message counts,
and an honest state banner - it deliberately never shows a "dead" empty
dashboard; if tickerplants are up but nothing's publishing, it says exactly
that and links you to enable a feed. The "● LIVE" / "○ offline" badge in the
top right reflects the WebSocket connection itself, not the cluster's
health - if it's stuck on offline while the rest of the app clearly works,
that's a stale browser tab, not a backend problem (see the troubleshooting
guide).

## Topology / Tickerplants

**Topology** — every managed container (per shard: tp/wdb/rdb/idb/hdb, plus
gateway) with start/stop/restart controls and live logs. This is the same
control surface watchdog's auto-heal uses programmatically.

**Tickerplants** — a narrower, tp-focused view: publish rate, subscriber
count, queue depth, log rotation state.

## Metrics

The detailed version of Overview's headline numbers, plus the transit-lag
panel: a per-shard, per-table breakdown of pipeline latency by stage
(`feed_to_tp`, `tp_to_rdb`, `rdb_to_gateway`, `tp_to_wdb_flush`) - this is
real, computed from the actual event timestamps flowing through the system,
not a synthetic health check. A stage reading `nan` almost always means that
shard's process is currently unreachable (mid-restart), not a metrics bug -
cross-check against the Cluster health panel before assuming otherwise.

## Alerts

Rule-based notifications derived from the same metrics stream - a stalled
feed, an elevated order-rejection rate, a shard falling behind. Not a
general-purpose alerting/paging integration (no PagerDuty/Slack webhook
today) - it's an in-app panel.

## Query workspace (`/query`)

Run q directly against any target - the gateway (which fans out and
federates across shards for you), a specific `rdb-*` shard, or a
tickerplant's live buffer. Features:

- **Real syntax highlighting + autocomplete** (CodeMirror 6) - context-aware
  (columns/tables outrank general vocabulary right after
  `from`/`where`/`by`/`select`), backed by a live `cols <table>` fetch
  against a reachable RDB, not just a static schema guess.
- **Plain-English → q** ("Describe it") - an LLM if one's configured
  (`NL2Q_LLM_PROVIDER`), falling back to an offline pattern-matcher so the
  box works with zero configuration.
- **Plain-English → a q function** ("Generate code") - same idea, for
  multi-line function definitions rather than a single query. No offline
  fallback (there's no safe regex generator for arbitrary functions).
- **Analyze** - before you run something, a deterministic pre-flight check
  flags two provable-not-guessed issues: whether your query's symbol filter
  lets the gateway skip shards it doesn't need to touch, and whether a
  `where`/`by` clause with no symbol filter is about to scan an entire
  table (and specifically, whether a non-aggregated `by` is about to copy
  every row into per-group lists - the single most expensive shape a query
  here can take). An LLM (if configured) adds an explanation, correctness
  nits, and suggested follow-ups on top.
- **Read-only by default.** A denylist blocks the obvious escapes
  (`system`, `hopen`, file/socket primitives, `set`/`upsert`, ...) as
  defense in depth - the real boundary is operational (point this at a
  restricted, read-only process). Writes need `QUERY_ALLOW_WRITE=1` on the
  deployment *and* an explicit opt-in per request.
- **Result limits are enforced at the query, not just the display** - a
  plain `select` gets a `#` take pushed into the query itself before it
  ever reaches kdb+, so an unbounded `select from trade` can't pull an
  entire multi-million-row table over the wire. A `where`/`by` clause still
  has to scan before it can filter or group, though - the cap can't shortcut
  that; narrow with a symbol filter for anything large.
- **Query cost governance** (opt-in, `QUERY_BUDGET_MS_PER_WINDOW`) - if a
  deployment has a per-tenant query budget configured, `GET
  /query/cost/summary` shows current consumption vs. budget; exceeding it
  blocks further queries with a 429 until older ones roll out of the
  window.
- **Export** - download the current grid as Parquet (local, capped at
  10GB), or kick off a background export to S3/ADLS for larger pulls
  (re-runs the query server-side against a much higher row ceiling, streams
  to storage, doesn't hold the whole result in memory).

A companion **VS Code extension** (`vscode-extension/`) runs the same
queries against the same backend from inside an editor, for anyone who'd
rather not context-switch to a browser tab.

## Query analysis

`/query/analysis` (distinct page from the Analyze button inline in the
workspace) - a dedicated space to paste/write a q expression and get the
same deterministic + LLM analysis without needing to run it against a live
target.

## Markets / Orders / Portfolio / Bot / Execution

The trading terminal. **Paper by default, unconditionally**, until you
deliberately configure otherwise - orders fill against a caller-supplied
reference price with no real order book or matching engine. Two real broker
seams exist:

- `BrokerRouter` (generic FIX/broker adapter) - still unconditionally
  refuses; nothing is wired to it.
- `IBKRRouter` (`app/ibkr_broker.py`) - order execution against a real
  Interactive Brokers account via its Client Portal Web API (CPAPI), same
  paper/live two-signal safety pattern as Alpaca below (`IBKR_TRADING_MODE`).
  Requires a locally-running, already-authenticated Client Portal Gateway -
  see that module's own docstring. IBKR is ALSO a market-data provider
  (`data-plane/feeds/providers/ibkr.py`, Level 1 quotes via the same
  gateway) - a separate concern from order execution, catalogued
  independently on the Connectors page.
- `AlpacaRouter` (`app/alpaca_broker.py`) - a real, working integration.
  Unconfigured (no `ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`, or
  `ALPACA_TRADING_MODE=off`, the default), behavior is identical to before
  this existed. `ALPACA_TRADING_MODE=paper` routes marketable fills (market
  orders, and limit orders that cross on arrival) through Alpaca's own
  paper-trading simulation instead - a real broker's order mechanics against
  live prices, still zero real money. `ALPACA_TRADING_MODE=live` moves real
  money and additionally requires `ALPACA_LIVE_TRADING_ACK` to exactly match
  a confirmation phrase (`alpaca_broker.LIVE_ACK_PHRASE`) - a bare
  `ALPACA_TRADING_MODE=live` cannot enable it by accident, and a
  missing/wrong ack silently downgrades to paper rather than either erroring
  or (worse) going live anyway. A resting (non-marketable) limit order
  always stays on the internal matcher regardless of mode - Alpaca's own
  order-book lifecycle isn't wired in for that case. The Orders and Bot
  pages both show the real active mode as a badge (PAPER / ALPACA PAPER / a
  loud red ALPACA LIVE), never a hardcoded label.

Going live on a product shipped to customers is a business/legal decision
needing real compliance review (broker connectivity + entitlements +
sign-off), not just a config change - see `alpaca_broker.py`'s own
docstring before ever setting `ALPACA_TRADING_MODE=live`.

A real pre-trade risk gate runs before every fill regardless of route,
reading the same live risk feed the Alerts page does - it fails **closed**
by default (an unreachable risk feed blocks the order rather than letting
it through unverified; `RISK_GATE_FAIL_OPEN` opts back into the old
fail-open behavior for desks that have consciously decided that tradeoff).
Trading permission is a separate, explicit grant (`can_trade`) from tenant
admin - viewing market data needs no special permission, placing orders
does.

**TradingView chart tab** (Markets page, "TradingView" tab) - an embedded
TradingView Advanced Chart widget (their own free, documented `tv.js`
embed). This is TradingView's own market data, not this platform's internal
tick feed - it's for cross-checking and TradingView's own indicator
library, alongside (not instead of) the real internal Chart tab. Ticker ->
`EXCHANGE:SYMBOL` mapping is a best-effort guess
(`components/TradingViewChart.jsx`'s `guessTradingViewSymbol`); there's a
manual override input for whatever it guesses wrong.

**TradingView alert webhooks** (`app/routers/tradingview_webhook.py`,
configured from a card on the Bot page) - a second, independent automated
order-placing surface: a TradingView alert (Pine Script strategy or manual)
can `POST` to a per-tenant URL
(`/webhooks/tradingview/{token}`) and place a real order through the exact
same `place_market_order_internal` path and pre-trade risk gate as every
other order in this codebase - no webhook-specific shortcut. Because
TradingView's alert webhooks cannot send a custom header or a signed body
on non-Enterprise plans, the `token` in the URL path is the *entire* auth
mechanism - treat it as a bearer credential, rotate it if it ever leaks
(the Bot page has a Rotate button). Two hard, server-enforced defenses
against a leaked token: an explicit per-tenant symbol allowlist (a webhook
can only ever trade symbols the tenant added, same "add a symbol before
enabling" guard as the signal bot's basket) and a hard `max_qty` cap
re-clamped server-side regardless of what the alert payload claims.

## Connectors

Turn real market-data providers on/off per tenant, scoped to a symbol
group. Two tiers, shown honestly: **live** (Finnhub, Twelve Data,
Polygon.io, Alpaca, Coinbase, Kraken, Yahoo Finance, Alpha Vantage - usable
today, several needing no API key at all) and **licensed** (NYSE,
LSEG/Refinitiv, NSE, BSE - coded to the real protocol/SDK shape but
refusing with a clear message until real credentials/entitlements are
plugged in; never fake a connection). Alpaca's market-data feed and its
order-routing seam (above) share credentials but are otherwise
independent - enabling one doesn't enable the other.

Each live provider's `docker-compose.yml` service takes `--symbols` (a
plain comma list, e.g. `ALPACA_SYMBOLS=AAPL,MSFT`) or `--symbols all` for
the providers whose venue exposes a real "give me every currently-tradable
instrument" endpoint (`fetch_all_symbols()` in that provider's own module -
the crypto venues plus Alpaca, whose `/v2/assets` returns its full active
US-equity/ETF universe, several thousand symbols, pulled live rather than
from a hardcoded list that would drift stale). A curated large-but-smaller
subset instead of literally everything: put it in a file under
`data-plane/feeds/symbols/` and set `PROVIDER_SYMBOLS_FILE` (see that
folder's own README). `all` means real breadth, not necessarily useful
density - most of a full equity universe rarely prints a trade.

## Autoscaling

Computes shard-count recommendations from real ingest-rate metrics against
a target-messages-per-shard policy you set, with a cooldown so it doesn't
thrash. Applying a recommendation calls through to Fleet's provisioning
path - it's advisory plus one click, not a fully closed automatic loop;
scaling shard count is a stateful topology change (a new shard starts
empty), not an instant, free operation.

## TickHouses

Declaratively define a tick cluster - cloud, OS, performance profile
(low-latency / high-throughput / balanced, each auto-tuning hardware and
kernel/NIC settings), sharding policy (letter-range or explicit
symbol-to-shard mapping), EOD hour, and retention policy (idb days, rdb live
window, hdb purge). `auto_spec` fills in a full, sized component list from
just name/cloud/OS/profile/shard-ranges - the "reduce admin overhead" path.
Provisioning a spec routes through Fleet to whichever backend the target
environment uses (local docker-compose, or a remote agent in the tenant's
own cluster).

## Fleet

Remote, per-tenant provisioning agents - one per customer environment
(cloud or on-prem), enrolled via a one-time token, polling for
start/stop/restart/provision commands. This is the mechanism a TickHouse
spec actually gets stood up through when the control plane has no direct
network path into a tenant's environment (the normal case for a real
multi-tenant SaaS, as opposed to this local demo where control-api talks to
the cluster directly).

## Migration assessment / TCO

Sales-engineering tooling, not a production platform feature. **Migration
assessment**: paste/upload q scripts, get a static-analysis scan (nothing
persisted, so client source never sits in this system). **TCO**:
auto-sizes a TickHouse spec, estimates infra cost from editable public
on-demand rates, and diffs it against a self-reported current spend figure
- it's a savings calculator against whatever number you give it, not a
benchmark against a named competitor's pricing.

## Data export / Subscribers / Audit log

**Data export**: configure S3/ADLS sinks for background query exports (see
Query workspace above). **Subscribers**: who's currently connected to a
tickerplant. **Audit log**: every admin action and notable system event
(service start/stop, connector toggles, order placement, provisioning) -
compliance/security trail, not the query-history/profiling data (that's a
separate, intentionally ephemeral in-memory ring buffer scoped to the query
workspace - see the Query workspace section above).
