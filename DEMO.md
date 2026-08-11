# Demo runbook — the 8(-ish)-minute story

This is the script to talk over in front of a prospect. It maps to
`python -m demokit.demo` plus the load-test, but the point is the *narrative*:
kdb+ gives you a fast engine and leaves you to build all the operational
scaffolding yourself — this is that scaffolding, working, wrapped in a UI
that doesn't look like an internal ops tool from 2015.

Know your audience: kdb+ shops are trading firms, banks, and energy/commodities
desks. The pain you're speaking to is real and personal — a tickerplant that
fell over at 3am, a slow consumer that took the feed down, a sharding scheme
someone hand-maintained until it drifted, an analyst who wanted last week's
history in a notebook and got a CSV export that took all afternoon.

## Before they arrive (10 min)

1. Stack up on the demo box: KDB-X binary + `k4.lic` in
   `data-plane/docker/kdbx/`, then `docker compose up -d` (see root README).
2. `pip install -r demokit/requirements.txt`.
3. Dry-run the whole thing once so nothing surprises you live:
   ```bash
   python -m demokit.demo --no-pause --no-colour --email admin@demo-bank.local --password <pw>
   ```
   Exit code 0 means api + orchestrator + watchdog + gateway all agree.
4. Have the web UI open (`http://localhost/` or your TLS domain) on the
   Overview tab, and a terminal ready.

## The live run

Run `python -m demokit.demo --pause 2 ...` and talk over each act:

**Act 1 — it's up.** Log in. "One control plane over the whole kdb+ estate —
API, UI, and a self-healing watchdog." The dashboard is dark, card-based, big
numbers up front — deliberately closer to what a trading desk actually uses
day to day than a classic ops console, because the people running this are
the same people who'll be looking at it during a live incident.

**Act 2 — the topology.** Point at the shards on the Topology tab. "This is
an N-way sharded tick setup — tickerplant, write-down DB, RDB, IDB per shard.
The shard count is a single number; everything — the processes, the gateway
routing, the volumes Helm provisions — derives from it. Nobody hand-maintains
a port list." (If asked, `SHARD_COUNT=4` regenerates the whole stack — show
`scripts/gen_topology.py`. If they're capacity-planning-minded, the
**Autoscaling** tab takes this further: a live shard-scaling recommendation
computed from real ingest volume, with per-shard sync status shown as honest
milestones, not a fabricated progress bar.)

**Act 3 — feeds on, data flowing.** "Turning on the B-PIPE-shaped feed. Watch
the row counts climb — the gateway is aggregating across shards for you."

**Act 4 — the money shot: kill something.** "Now I'll kill a tickerplant —
a process crash, no warning." The service goes red on the Topology tab. Wait.
"No one is paging anyone. The watchdog detected it, ran its restart runbook,
and here's the recovery in the audit log — actor `watchdog`, action
`auto_heal`." This is the beat that sells; let the silence sit while it heals.
The Audit log now reads as an activity feed (icon per action, watchdog entries
visually distinct) — it's the same trail, easier to scan live.

**Act 5 — the slow consumer (optional, do it live if they're technical).** In a
second terminal:
```bash
python -m demokit.load_test slow-sub --tp-host localhost --tp-port 5010
```
"A classic kdb+ outage: one slow subscriber backs up the tickerplant's queue
and takes the whole feed down. Watch — its queued bytes climb, it takes its
strikes, and the tickerplant sheds it before it can hurt anyone else." The
discard shows up in the Audit tab too.

## Bonus material (if there's time, or for a technical/quant audience)

**The query workspace.** Open **Query**, type `select from trade where s` and
let the autocomplete pop up — "that's not a hardcoded keyword list, it just
read the real column names off a live RDB." Run something, then click
**Download Parquet** — "that's a real Parquet file, built from exactly what's
on screen." If they ask about bulk history: "for anything bigger than a
browser download, there's a background export straight to S3 or ADLS, with
real upload progress, that checks the gateway isn't already under load before
it runs."

**The trading terminal.** Markets → Orders → Portfolio → Bot. "This exercises
the same live query path with realistic market UI — candlesticks, a
calendar-horizon forecast, a portfolio view, even a small risk-capped paper
bot." Say plainly, before they ask: **paper only, permanently** — no bank
account, no broker, by design, because the market data underneath is
synthetic. This is a demonstration of the query/data path under a familiar
UI shape, not a trading product.

## The numbers (separate session, or leave running)

```bash
python -m demokit.load_test throughput --shards 2 --start-rps 1000 \
  --stop-rps 20000 --step-rps 1000 --step-seconds 20 --report result.md
```
"This is throughput I measured on *this* box — offered load vs what the data
plane actually ingested, and the exact point it starts shedding. I don't quote
a number I haven't measured on your target hardware." Hand them `result.md`.

## What to admit before they ask (it builds trust)

- The market data is synthetic — it exercises the plumbing under a realistic
  load shape, not a live vendor feed. (Finnhub/Twelvedata connectors are real
  but opt-in and delayed/free-tier — mention if asked, don't lead with it.)
- Throughput is hardware/shard/version dependent; you'll re-measure on their spec.
- The trading terminal never routes anywhere real, on purpose — real money
  against synthetic prices would be actively harmful, not just incomplete.
- Background export to S3/ADLS needs real cloud credentials configured on the
  server first; it fails with a clear message without them, it doesn't fake success.
- This is a working demo of the architecture, not a hardened multi-tenant
  product yet — be clear on where the line is (the root README's limitations
  section is the honest inventory).

## If something breaks mid-demo

- Feed not climbing → check the connector is enabled (Connectors tab) and a
  feed sim container is up.
- Watchdog didn't heal in time → raise `--heal-timeout`; check the watchdog
  container logs on the Topology tab.
- Gateway unreachable → the demo tolerates it (reads 0), but ingest won't show;
  restart `gateway` from the Topology tab.
- Autocomplete showing no columns → the live schema fetch needs a reachable
  RDB target (it deliberately avoids asking the gateway, which has no table of
  its own) - confirm at least one `rdb-*` process is running.
- Query export to S3/ADLS stuck on "uploading" → expected if no cloud
  credentials are configured on this box; it'll resolve to a clear failure,
  not hang forever - fine to let it fail live and explain why, it's honest
  behavior, not a bug.
- Overview page stuck on "○ offline" / "waiting for data" even though the
  cluster is actually healthy (confirmed via `/metrics/snapshot` or a direct
  q query) → on the local-tls deploy (`deploy/tls/docker-compose.local-tls.yml`,
  self-signed cert from Caddy's own CA) this is almost always the browser
  silently refusing the `wss://` metrics stream because it doesn't trust that
  cert - clicking through the page's own "Not Secure" warning does NOT
  reliably extend to a JS-initiated WebSocket subresource, so the socket just
  never opens and the dashboard sits frozen at zero. A hard refresh / clearing
  cookies does NOT fix this (confirmed - it's a cert-trust problem, not a
  cache problem). Fix: trust Caddy's local root CA once, machine-wide, then
  fully quit and reopen the browser (not just reload - the trust store is
  only read at process start):
  ```
  docker cp kdb-control-plane-caddy-1:/data/caddy/pki/authorities/local/root.crt /tmp/root.crt
  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain /tmp/root.crt   # macOS
  ```
  Verify the server side independently of the browser with:
  `curl -sk -i --http1.1 -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" --resolve <domain>:443:127.0.0.1 https://<domain>/api/metrics/stream`
  - a `101 Switching Protocols` followed by streaming JSON means the backend
  is fine and this is purely the browser's cert trust.
- (Fixed, but worth knowing if it resurfaces differently) The same "offline"
  symptom can also come from two now-patched bugs: `metricsSocket()` in
  `web-ui/src/api.js` used to have zero reconnect logic, so ANY transient
  disruption (a redeploy, a container restart, a brief gateway hiccup)
  killed the live dashboard permanently until a manual reload - it now
  auto-reconnects with backoff. And `/metrics/stream` (`control-api/app/routers/metrics.py`)
  used to call the gateway synchronously inside the async websocket loop,
  which could block the ENTIRE server's event loop (not just metrics) if the
  gateway was ever slow to answer - it's now offloaded via `asyncio.to_thread`.
