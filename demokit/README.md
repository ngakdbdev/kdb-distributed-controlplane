# demokit — sales-demo toolkit

Two operator tools for showing the control plane to a prospect, plus a pure,
unit-tested core so the measurement logic is trustworthy even though the
numbers themselves can only come from a real deployment.

| tool | drives | over | shows |
|---|---|---|---|
| `python -m demokit.demo` | the control-API | HTTP | up → topology → feeds → **kill a TP → watchdog heals it** → audit trail |
| `python -m demokit.load_test throughput` | the data plane | kdb+ IPC | offered vs achieved ingest, and where it starts shedding |
| `python -m demokit.load_test slow-sub` | a tickerplant | kdb+ IPC | queue growth → strikes → **auto-discard**, proven with numbers |

Nothing here bundles KDB-X. The tools connect to a stack you've already stood
up per the root README (download KDB-X yourself, `docker compose up`).

## The narrated demo

```bash
python -m demokit.demo \
  --base-url http://localhost:8000 \
  --email admin@demo-bank.local --password <demo-tenant-pw> \
  --pause 2
```

It logs in as the **tenant** admin (connectors/metrics are tenant-scoped),
walks the five acts with a pause between each so they land on screen, and
exits non-zero if any check fails — so the same command doubles as a CI smoke
test of the whole stack with `--no-pause --no-colour --no-chaos`.

The self-healing act stops `tp-s0` through the API, watches the topology show
it DOWN, then polls until the watchdog restores it and reads the matching
`auto_heal` row straight out of `/audit`. Change the victim with
`--chaos-service`.

## The load-test

```bash
# ramp 1k → 20k rows/s in 1k steps, 20s each, against a 2-shard stack
python -m demokit.load_test throughput \
  --shards 2 --start-rps 1000 --stop-rps 20000 --step-rps 1000 \
  --step-seconds 20 --tp-host-pattern 'tp-{shard}' --gateway-host localhost \
  --report result.md
```

It reuses the **real** `ShardedPublisher` and `bpipe_sim` generator, so it
exercises the same fan-out-across-shards path a production feed uses — not a
parallel reimplementation. Each step reports:

- **published/s** — what the client actually pushed
- **ingest/s** — what landed in the RDBs, read via `.gw.health[]` (the same
  numbers the dashboard shows)
- **loss** — the gap between them; a step "keeps up" at ≤ 2% loss
- **peak sustained ingest** — the fastest rate it absorbed *without* shedding

That last figure is the only throughput number worth quoting to a prospect —
and it's one you measured on your hardware, not a slide.

### Slow-subscriber discard

```bash
# in one shell: make sure a feed is running (demo enables it, or bpipe_sim)
python -m demokit.load_test slow-sub --tp-host localhost --tp-port 5010 \
  --read-every-s 5 --window-seconds 120
```

Attaches a subscriber that services its socket far too slowly, then prints its
queued bytes climbing and strikes accruing until `tick.q` drops it — the same
event surfaces in the control-plane **Audit** tab as `slow_sub_discard`.

## Why the split (and why it's testable)

`demokit.harness` is pure: `RateStep`, the publish/ingest accounting, loss and
peak-sustained calculations — no kdb+, no sockets. `demokit/tests/` drives it
and the demo runner with fakes and asserts every number, so `pytest demokit`
is green with nothing installed but pytest. The IPC/HTTP layers
(`kdb_probe`, `feed_publisher`, `api_client`) are thin glue you point at a
live stack.

```bash
pip install -r demokit/requirements.txt   # only needed for the live tools
python -m pytest demokit                   # the core needs only pytest
```

## Honest caveats — say these out loud

- The market data is synthetic (`bpipe_sim`), so throughput reflects the tick
  plumbing under a realistic *shape* of load, not a specific vendor feed.
- Numbers depend entirely on your hardware, shard count, and KDB-X version.
  Re-run on the prospect's target spec; don't carry a number between demos.
- `loss` counts rows published-but-not-ingested within a step's window; a
  little apparent negative loss (drain from the previous step) is clamped to 0.
