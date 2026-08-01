# Demo runbook — the 8-minute story

This is the script to talk over in front of a prospect. It maps to
`python -m demokit.demo` plus the load-test, but the point is the *narrative*:
kdb+ gives you a fast engine and leaves you to build all the operational
scaffolding yourself — this is that scaffolding, working.

Know your audience: kdb+ shops are trading firms, banks, and energy/commodities
desks. The pain you're speaking to is real and personal — a tickerplant that
fell over at 3am, a slow consumer that took the feed down, a sharding scheme
someone hand-maintained until it drifted.

## Before they arrive (10 min)

1. Stack up on the demo box: KDB-X binary + `k4.lic` in
   `data-plane/docker/kdbx/`, then `docker compose up -d` (see root README).
2. `pip install -r demokit/requirements.txt`.
3. Dry-run the whole thing once so nothing surprises you live:
   ```bash
   python -m demokit.demo --no-pause --no-colour --email admin@demo-bank.local --password <pw>
   ```
   Exit code 0 means api + orchestrator + watchdog + gateway all agree.
4. Have the web UI open (`http://localhost/`) on the Topology tab, and a
   terminal ready.

## The live run

Run `python -m demokit.demo --pause 2 ...` and talk over each act:

**Act 1 — it's up.** "One control plane over the whole kdb+ estate — API, UI,
and a self-healing watchdog. I'm logging in as a tenant admin."

**Act 2 — the topology.** Point at the shards. "This is an N-way sharded tick
setup — tickerplant, write-down DB, RDB, IDB per shard. The shard count is a
single number; everything — the processes, the gateway routing, the volumes
Helm provisions — derives from it. Nobody hand-maintains a port list." (If
asked, `SHARD_COUNT=4` regenerates the whole stack — show `scripts/gen_topology.py`.)

**Act 3 — feeds on, data flowing.** "Turning on the B-PIPE-shaped feed. Watch
the row counts climb — the gateway is aggregating across shards for you."

**Act 4 — the money shot: kill something.** "Now I'll kill a tickerplant —
a process crash, no warning." The service goes red on the Topology tab. Wait.
"No one is paging anyone. The watchdog detected it, ran its restart runbook,
and here's the recovery in the audit log — actor `watchdog`, action
`auto_heal`." This is the beat that sells; let the silence sit while it heals.

**Act 5 — the slow consumer (optional, do it live if they're technical).** In a
second terminal:
```bash
python -m demokit.load_test slow-sub --tp-host localhost --tp-port 5010
```
"A classic kdb+ outage: one slow subscriber backs up the tickerplant's queue
and takes the whole feed down. Watch — its queued bytes climb, it takes its
strikes, and the tickerplant sheds it before it can hurt anyone else." The
discard shows up in the Audit tab too.

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
  load shape, not a live vendor feed.
- Throughput is hardware/shard/version dependent; you'll re-measure on their spec.
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
