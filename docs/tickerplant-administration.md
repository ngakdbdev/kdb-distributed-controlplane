# Tickerplant administration

How the tick chain actually works, how to operate it day to day, and the
knobs you have. This is the deep-technical companion to
`docs/platform-usage.md`'s Topology/Tickerplants sections. For "it's broken,
what do I do," see `docs/troubleshooting.md`.

## The chain, per shard

```
feed (sim or real provider) --publish--> tickerplant (tp)
                                              |  relays live + appends to a log
                          +-------------------+-------------------+
                          v                                       v
                    wdb (write-down)                        rdb (chained RDB)
                    buffers, periodically                   warm-starts from wdb's
                    flushes to a scratch                    scratch file, then serves
                    file, seals each day                    live in-memory queries,
                    into the hdb at EOD                     sheds old rows per watermark
                          |
                          v
                    hdb (historical) <---- idb (intraday batch)
                    memory-maps sealed                cache of just-sealed days,
                    partitions, serves                bridges the gap right after
                    pre-today queries                 EOD before hdb's next reload
```

**gateway** sits in front of all shards, federating queries and exposing
health/metrics RPCs (`.gw.health[]`, `.gw.transitLag[]`,
`.gw.componentMetrics[]`) the control plane polls.

Every process is a single q instance per shard per tier - there is no
intra-tier clustering (one `rdb-s0`, not a pool). Scaling is by shard count
(more, narrower shards), not replicas within a shard.

## Sharding

Symbols partition by contiguous first-letter range (`topology.py`,
canonical in `control-api/app/topology.py`) - at 2 shards, `s0` owns A-M,
`s1` owns N-Z. `topology.shard_of(sym, n)` is the single source of truth
every tier uses to decide which shard a symbol belongs to (feed handlers
tag rows with it at publish time; the gateway uses the identical mapping to
route). Changing shard count is a real topology change requiring
regeneration (`scripts/gen_topology.py`) and doesn't rebalance existing
data - a new shard starts empty.

**A real trap this exact deployment hit**: synthetic filler symbols (used
to pad a small universe up to `SIM_SYMBOL_COUNT` for load testing) used to
all share one prefix (`SYN00001`, `SYN00002`, ...) - meaning they *all*
hashed to whichever single shard owns that letter, silently dumping nearly
all synthetic load onto one shard while others sat idle. Fixed by cycling
the prefix through the full alphabet (`data-plane/feeds/feed_common.py`'s
`build_universe`) so padding spreads evenly regardless of how many shards
are configured. Any custom symbol-generation logic you add should do the
same - check the actual per-shard row counts (`count trade` on each
`rdb-*`), don't assume even distribution.

## Retention and end-of-day

Three independent knobs, each with a different scope - don't confuse them:

| Knob | Scope | Flag | Default |
|---|---|---|---|
| `flushmin` | how often wdb writes its buffer to the scratch file (frees wdb's own memory) | `wdb.q -flushmin` | 2 min |
| `retentionmin` | how much live data the *chained RDB* keeps in memory | `wdb.q -retentionmin` | 2 min (== flushmin) |
| `retentiondays` | how many *sealed* days the HDB keeps on disk before purging | `hdb.q -retentiondays` | 0 (keep forever) |

`retentionmin` must be `>= flushmin` - wdb enforces this at startup (a
shorter retention than flush cadence would tell the RDB to shed data that
hasn't been durably flushed yet). Raising `retentionmin` gives the RDB a
longer live query window at the cost of more memory and slower warm-start
recovery (see below).

**What retention does NOT bound**: the on-disk scratch file wdb appends to
throughout the day. That file only shrinks at end-of-day sealing - it is
*not* trimmed by the retention watermark, which only governs the RDB's
in-memory table. A day that's run at high tick volume for many hours will
have a large scratch file regardless of how tight `retentionmin` is set,
and RDB re-reads that entire file on every warm-start. This is the root
cause behind the OOM-crash-loop pattern in the troubleshooting guide - know
it's expected behavior, not a bug, and plan tick-rate/uptime accordingly
until a real fix (bounding the scratch file itself, at the cost of
incomplete EOD history) is worth the tradeoff for your use case.

**EOD** fires automatically at real wall-clock UTC (`-eodhour`, default
midnight), self-triggered by each tickerplant's own timer - no external
cron needed. It broadcasts to every subscriber: wdb force-flushes and seals
the closing day into the hdb (via `.Q.dpft`, sorted+enumerated by `sym`),
rdb defensively sheds anything before the new watermark, and the
tickerplant rotates its log file. `.u.end[d]` (q-side, on a tickerplant) can
force this manually for testing without waiting for real midnight.

## Adaptive thread sizing

`rdb`/`wdb`/`hdb` reserve kdb+ secondary threads (`-s`) sized from each
container's own visible CPU count at boot (`KDB_THREADS=auto`, capped at 4
by default - confirmed empirically as this deployment's licensed max;
raise the cap in `data-plane/docker/kdb-entrypoint.sh` only after
confirming a higher licensed max on your own box). Override per-tier with
`RDB_THREADS`/`WDB_THREADS`/`HDB_THREADS` env vars, or pin a literal
integer instead of `auto`.

`rdb.q` specifically **autoscales down** after warm-start: it peaches
across the full reserved ceiling to parallelize loading `trade`/`risk` off
disk, then drops to 1 active thread once recovery completes, since
steady-state tick ingestion is single-threaded and never touches the rest.
Watch for `"warm-start recovery using N secondary thread(s)"` then
`"recovery complete, scaled secondary threads N -> 1"` in its logs to
confirm this is behaving.

## Health and metrics

Every tier exposes a `.<tier>.health[]` RPC (or `.u.stats` for the
tickerplant) with process-specific fields - row counts, connection state,
reconnect attempts, last-seen timestamps. `gateway.q`'s `.gw.health[]`,
`.gw.componentMetrics[]`, and `.gw.transitLag[]` fan out across every shard
and normalize the result for the control plane. Query any of these directly
for a raw view when the UI's abstraction isn't enough:

```
q).gw.health[]                 / every shard, every tier, one row each
q).gw.transitLag[]             / pipeline latency by stage, per shard/table
q).rdb.health[]                 / (on an rdb-* process directly) - row counts, watermark, reconnects
```

## Watchdog

A separate daemon (`watchdog/`) polls every managed container's status
every `WATCHDOG_POLL_SEC` (default 5s) and runs a runbook
(`watchdog/runbooks.py`) on failure: `container_down` → restart and verify
(up to 3 attempts), `dependency_down` → defer until the dependency itself
recovers, `flapping` (3+ restarts within its detection window) → escalate
and stop auto-restarting rather than fight a real, ongoing problem forever.

**When you need to intervene manually** (a genuine incident watchdog can't
resolve on its own - see the troubleshooting guide's OOM section), **stop
watchdog first** (`docker compose stop watchdog`) so it isn't racing your
own recovery attempt, do the fix, confirm stability, then
`docker compose start watchdog` again. Skipping this step is the single
most common way a manual recovery attempt turns into a longer outage - two
independent restart loops (watchdog's and yours) compound instead of
resolving.

## Common administrative tasks

**Check a shard's real row counts and watermark:**
```bash
docker exec <control-api container> python3 -c "
from qpython import qconnection
c = qconnection.QConnection(host='rdb-s0', port=5020, pandas=False, timeout=8)
c.open()
print('count trade:', int(c('count trade')))
print('watermark:', c('.rdb.watermark'))
c.close()"
```

**Force an EOD roll for testing** (on the tickerplant, not rdb/wdb):
```
q).u.end[.z.d]
```

**Change tick rate** (feed simulators, not real providers): `BPIPE_RATE_HZ`
/ `CRIMS_RATE_HZ` in `.env`, then `docker compose up -d bpipe-sim
crims-sim` to pick it up.

**Run a one-off q script against a real target's disk state without going
through the normal service startup** (used for the destructive-but-tested
scratch-file trim in the troubleshooting guide):
```bash
docker compose run --rm -T <service> -q   # pipe a script via stdin
```

**Add a real market-data provider**: Connectors page (per-tenant, symbol
group scoped), or directly via `python -m providers.runner --provider
<name> --symbols ...` - see `data-plane/feeds/providers/README.md` and
`docs/developer-guide.md`'s "Adding a provider" section.
