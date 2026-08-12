# Troubleshooting

Every entry here traces back to a real, reproduced incident on this
deployment, not a hypothetical. Organized by symptom. Each entry gives the
real cause and the fix - not just "restart it," though sometimes that
genuinely is the answer, and this says so plainly rather than pretending
otherwise.

**Before anything else**, check the actual container state - most
"something's wrong" reports turn out to be one specific process cycling,
not the whole platform:

```bash
docker compose ps --format "table {{.Name}}\t{{.Status}}"
docker inspect <container> --format 'RestartCount={{.RestartCount}} OOMKilled={{.State.OOMKilled}}'
```

A high `RestartCount` (dozens or more) means a *chronic* problem, not a
one-off blip - see the OOM section below, the single most common chronic
cause on this platform.

---

## "select from trade by sym" (or similar) fails with a one-word error like `'sym`

**Not a timeout, not corrupted data.** It's q-sql's clause order: `select
[cols] by [groupcols] from table [where ...]` — `by` must come before
`from`. Put it after `from` and q doesn't throw a parse error (so it looks
like it should work), it silently evaluates through a different path and
fails trying to resolve the group column as a global - the error is just
named after whatever column you grouped by (`'sym`, `'venue`, whatever you
used).

Confirm it's this and not a real infra issue by checking the actual
response time - a genuine grammar rejection comes back in under a
millisecond:

```
select from trade by sym    -> ERR 'sym     (from before by - wrong)
select by sym from trade    -> OK           (by before from - correct)
```

## Query workspace: "Target path unavailable... did not answer in time"

Two completely different situations produce this exact message, and only
one of them is real:

1. **A real connectivity failure** - the message is accurate.
2. **Every target rejected the query for the same reason** (e.g. the
   clause-order issue above) - the backend returns HTTP 502 for "all
   targets failed" regardless of *why* they failed, and the frontend used
   to show the generic "unreachable" framing for any 502. Fixed: the
   formatter now only shows that framing when there's no `query error:`
   signal in the underlying message (see `web-ui/src/pages/Query.jsx`'s
   `formatQueryError`).

Check `/query/history` (or the backend's query-profile log) for the actual
per-target error text before assuming it's infrastructure - it's usually
right there.

## Query workspace: request genuinely times out / hangs

If it's a plain `select` with no `where`/`by`: this used to be a real gap -
the row `limit` field only truncated the result *after* the entire table
had already been pulled over IPC. Fixed (`query_service._cap_result_rows`)
- a plain select now gets a `#` take pushed into the query itself.

If it's a `where`/`by` query: this is real, and the cap above **cannot**
help it - kdb+ has to scan (and for a non-aggregated `by`, fully copy) the
data before it can filter or group, regardless of how the result gets
truncated afterward. Narrow with a symbol filter (`where sym in (...)`), or
use the Analyze button before running - it flags exactly this pattern
deterministically, no LLM required.

## A `select ... by ...` query's result renders as garbled text instead of a grid

Was a real bug, now fixed: `select ... by ...` comes back from the qpython
client library as a `QKeyedTable`, which is a *separate* class from
`QDictionary` despite being structurally identical - the result-shaping
code only checked for `QDictionary`, so every grouped query fell through
to the last-resort fallback and got stringified whole. Fixed in
`query_service.shape_result`. If you see this again, check whether a new
qpython result type needs the same treatment.

## Overview page stuck on "○ offline" / "waiting for data" despite a healthy cluster

Confirm the cluster is actually fine first (`/metrics/snapshot`, or query a
shard directly) - if it is, this is specifically the live metrics
**WebSocket**, not the platform. Two real causes, both fixed but worth
knowing:

- **On the local-TLS setup** (self-signed cert from Caddy's own CA): a
  browser's "click through the warning" trust for the main HTTPS page
  doesn't reliably extend to a JS-initiated `wss://` connection to that
  same untrusted cert - the socket just silently never opens. A hard
  refresh does **not** fix this (confirmed - it's a cert-trust problem,
  not a cache problem). Fix: trust Caddy's local root CA machine-wide, then
  fully quit and reopen the browser (not just reload):
  ```bash
  docker cp <caddy-container>:/data/caddy/pki/authorities/local/root.crt /tmp/root.crt
  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain /tmp/root.crt   # macOS
  ```
  Verify the server side independently of the browser:
  ```bash
  curl -sk -i --http1.1 -H "Connection: Upgrade" -H "Upgrade: websocket" \
    -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
    --resolve <domain>:443:127.0.0.1 https://<domain>/api/metrics/stream
  ```
  A `101 Switching Protocols` followed by streaming JSON means the backend
  is fine and it's purely the browser's cert trust.
- **No reconnect logic** (fixed): `metricsSocket()` used to open once with
  no retry - any transient disruption (a redeploy, a container restart, a
  brief gateway hiccup) killed the live dashboard permanently until a
  manual page reload, even minutes after the backend had fully recovered.
  Now auto-reconnects with backoff (1s → 15s cap).
- **A slow gateway call could freeze the whole server** (fixed): the
  metrics WebSocket loop used to call the gateway synchronously, directly
  on the async event loop - a slow/hanging gateway call blocked *every*
  other concurrent request on the process, not just metrics. Now offloaded
  via `asyncio.to_thread`.

## `transitLag` metrics panel shows `nan` for one shard

Check that shard's containers are actually up
(`docker compose ps`) before assuming it's a metrics bug - `nan` is the
*correct* behavior when the gateway can't reach a down shard, not a
computation error. `tp_to_wdb_flush` specifically reading `nan` even on a
healthy shard usually just means wdb hasn't completed its first flush
cycle since its own last restart yet (a 2-minute-by-default wait) - check
its logs for a `"flushed at ..."` line before worrying.

## An RDB (or wdb/tp) is OOM-crash-looping, `RestartCount` in the dozens or hundreds

**The most common chronic failure on this platform.** Root cause chain:

1. High sustained tick volume (real or simulated) accumulates a large
   on-disk scratch file over the course of a trading day - this is
   *expected*, not a bug (see `docs/tickerplant-administration.md`'s
   retention section - the scratch file only shrinks at EOD sealing, not
   by the live-retention watermark).
2. Every RDB restart re-reads that entire file during warm-start
   (`.rdb.loadWarm`) - at tens of millions of rows, this can exceed
   available container memory.
3. **watchdog detects the resulting crash and restarts it again** - often
   *before* warm-start would have finished on its own, extending the
   outage indefinitely rather than giving one attempt a chance to
   complete. Confirm this is happening via `docker logs watchdog`: repeated
   `detected <target> (status=restarting) - signature=container_down` /
   `restart_and_verify` cycles.

**Fix, in order:**

```bash
# 1. Stop watchdog so it stops racing your recovery attempt
docker compose stop watchdog

# 2. Trim the bloated scratch file to a small recent window (synthetic/demo
#    data - safe to discard old rows; DON'T do this against data you need
#    the full history of without archiving it first)
printf '%s\n' \
  'f:`:/data/db/<TODAY_DATE>/trade' \
  't:@[get;f;0#0!([] time:`timestamp$();sym:`symbol$();price:`float$();size:`long$();side:`symbol$();venue:`symbol$();shard:`symbol$())]' \
  'show "before: ",string count t' \
  'cutoff:(max t`time) - 0D00:05:00' \
  't2:select from t where time>cutoff' \
  'show "after: ",string count t2' \
  'f set t2' \
  '\\' \
| docker compose run --rm -T <rdb-service> -q

# 3. Restart cleanly and confirm it stabilizes
docker restart <rdb-container>
sleep 10 && docker inspect <rdb-container> --format 'Status={{.State.Status}} RestartCount={{.RestartCount}}'

# 4. Resume watchdog once stable
docker compose start watchdog
```

**Root-cause fixes already shipped** that reduce how often this happens
(but don't eliminate the fundamental scratch-file-grows-until-EOD
characteristic): the synthetic symbol generator no longer skews all load
onto one shard (see the sharding section in the admin guide), and
`retentionmin` bounds the *live* table size between restarts. Neither
bounds the on-disk file warm-start has to re-read - that's the real,
still-open gap.

## A subscriber (rdb/wdb) keeps failing "tp connect/resubscribe failed", tickerplant shows nonzero queue depth/subscriber lag despite low real message volume

**Root cause, confirmed live**: `tick.q`'s `.u.sub` used the standard
kdb-tick pattern of returning `(t; value t)` on subscribe - the table's
*entire current in-memory content* - so a cold subscriber can seed its view
from it. Neither `rdb.q` nor `wdb.q` in this codebase actually use that
return value (both warm-start from on-disk files instead, and both
discarded it) - but the tickerplant's own `trade`/`risk` tables were never
purged (nothing in the codebase reads their accumulated rows otherwise), so
they grow for the whole trading day. Confirmed on a live box: `tp-s0`'s
`trade` table had reached 7M+ rows, so *every single reconnect* - a router
restart, a brief network blip, anything that makes a subscriber resubscribe
- synchronously shipped that entire 7M-row table over IPC as part of the
handshake before the subscriber could receive its next real update. That
transfer is slow enough to time out or reset the connection outright,
which triggers another reconnect attempt, which triggers another multi-
million-row transfer - a self-reinforcing loop that gets *worse* the
longer the tickerplant has been running, not better. It also directly
explains elevated `tpQueue`/`tpSubLag` on the dashboard even when real
message volume is low: the pressure was from the handshake payload, not
from ingest throughput.

**Fixed** (see `data-plane/q/tick.q`): `.u.sub` now returns just `t`
(acknowledgement only, no snapshot), and `.u.doUpd` no longer inserts into
the tickerplant's own `trade`/`risk` tables at all (nothing needs it - the
TP log file remains the real disaster-recovery record). This requires a
tickerplant restart to take effect - restarting also clears whatever's
already accumulated in memory. If you're on an older build without this
fix and need a stopgap: restarting `tp-*` periodically (before its
in-memory table gets large) reduces how expensive each subsequent
reconnect is, but doesn't fix the pattern.

## Symbol/shard load is wildly uneven (one shard's containers much busier than others)

Check the actual per-shard row counts and growth rate first
(`count trade` on each `rdb-*`, sampled a few seconds apart). If synthetic
filler symbols (`SIM_SYMBOL_COUNT`) all share one prefix, they all hash to
the same shard - see `docs/tickerplant-administration.md`'s sharding
section. Fixed in `feed_common.py`'s `build_universe` (cycles the prefix
through the alphabet), but any custom symbol generation you add needs the
same care.

## `docker compose up -d <service>` fails with "port is already allocated" (port 80)

You're running the TLS overlay (Caddy fronting the stack) but issued a
plain `docker compose` command without it. The base `docker-compose.yml`
publishes `web-ui` directly on host port 80; the TLS overlay
(`deploy/tls/docker-compose.*.yml`) unpublishes it because Caddy is
supposed to be the only thing on 80/443. Always include **both** `-f`
flags together once the overlay is active:

```bash
docker compose -f docker-compose.yml -f deploy/tls/docker-compose.local-tls.yml up -d <service>
```

Check `docker compose ps` for a running `caddy` container before assuming
which mode you're in.

## Caddy logs `"dial tcp: lookup web-ui on 127.0.0.11:53: no such host"` → 502

A transient Docker embedded-DNS blip during a `web-ui` container
recreation (the old container is gone, DNS hasn't caught up before the new
one registers). Self-resolves within seconds once the new container is up
- if it persists, confirm `web-ui` is actually running
(`docker compose ps`), not genuinely down.

## Web UI still shows old behavior after a code change and redeploy

`web-ui` is a static production build (`vite build` baked into the image at
`docker build` time) served by nginx with strict caching rules - `index.html`
is never cached (so a fresh load always picks up the latest build), but you
do need to actually rebuild the image, not just restart the container:

```bash
docker compose build web-ui
docker compose -f docker-compose.yml -f deploy/tls/docker-compose.local-tls.yml up -d web-ui
```

A plain `docker compose up -d web-ui` without rebuilding first just
restarts the *old* image. If it still looks stale after a real rebuild+
redeploy, hard-refresh the browser (`Cmd`/`Ctrl`+`Shift`+`R`) to rule out a
mid-air request that started before the redeploy landed.

## Pre-trade risk check never seems to actually block a trade

Was a real, previously-invisible bug: the risk check queried the
**gateway** process directly (`select from risk where sym=...`), but
gateway is a pure router with no `risk` table of its own - the query
always errored, and the old fail-*open* default silently treated every
failure as "checked, no breach found." Fixed: now queries the RDB shard
that actually owns the symbol (`topology.shard_of`), and defaults to
fail-**closed** (an unreachable risk feed blocks the trade rather than
passing it through unverified) - `RISK_GATE_FAIL_OPEN` opts back into the
old behavior if a desk has consciously chosen that tradeoff. If a risk
check is blocking everything, check `/query/history`-style logs (or the
audit log's `risk_gate_degraded` entries) for whether it's a real BREACH or
the feed being genuinely unreachable.

## A container needs surgery but you don't want to touch the running service

Use `docker compose run --rm -T <service> -q` piped a script via stdin -
gets a real q process with the service's actual environment/volumes
without running through its normal startup (no tickerplant subscription,
no listening on the service port), safe for one-off inspection or repair
against real on-disk state.
