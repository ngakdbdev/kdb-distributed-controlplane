# Getting started

This guide assumes nothing. If you've never used this codebase, never used
kdb+/q, or aren't sure what a "tickerplant" is, start here. By the end you'll
have the whole system running on your own laptop, understand what each piece
does, and know where to go next.

If you've done this before and just need the commands, use
[deployment-process.md](deployment-process.md) instead — it's the terse
version of this same guide.

## What is this, in plain English

Vantik is a control plane for capturing and querying **market tick data** —
every trade print (price, size, timestamp) flowing off an exchange or data
vendor, in real time, at high volume. "Tick data" just means "one row per
event" — a trade happens, a row gets written, forever, all day.

The engine underneath is **kdb+**, a database built specifically for this
(time-series, extremely high write throughput, extremely fast time-range
queries). kdb+ is programmed in its own language, **q**. You do not need to
know q to follow this guide — the web UI and this guide only ask you to run
shell commands (`docker`, `git`, `curl`) that anyone comfortable with a
terminal already knows.

Vantik is the *layer on top* of kdb+: a web UI, an API, and a self-healing
watchdog that together let you see the data flowing, run queries, manage the
infrastructure, and (in a paper-trading sandbox) place simulated orders
against it — without hand-writing q or SSH-ing into boxes.

## The five pieces of a tick pipeline, in one paragraph each

You'll see these five names constantly in the UI and the rest of the docs.
Here's what each one actually does, in order of how a trade flows through
them:

1. **Feed handler** — the thing that actually receives market data (from a
   real exchange, a real vendor, or — for this demo — a built-in *simulator*
   that invents realistic-looking fake trades). It hands each trade off to
   the next piece.
2. **Tickerplant (tp)** — the front door. Every trade arrives here first. It
   does three things with each one: writes it to a durable log file (so
   nothing is lost if something crashes), and relays it live, in real time,
   to whichever of the next two pieces are listening.
3. **RDB** ("real-time database") — holds *today's* trades in memory, so
   queries against "what's happened today" are instant. This is what backs
   the live dashboards and the query workspace by default.
4. **WDB** ("write-down database") — periodically saves what the RDB is
   holding out to disk, and at the end of each trading day, seals the whole
   day into permanent storage (the **HDB**, historical database) for
   querying weeks or years later.
5. **Gateway** — sits in front of all of the above and answers to the web UI
   and API. You (and the UI) mostly talk to the gateway, not to any
   individual piece directly — it figures out which piece(s) actually have
   the data your query needs and fans out to them.

One more term you'll see everywhere: **shard**. At any real trading volume,
one tickerplant/RDB/WDB per whole alphabet isn't enough — so the symbol
space gets split into ranges (e.g. "A through M" and "N through Z" for 2
shards) and each range gets its *own* full tp/RDB/WDB/HDB set, running in
parallel. More shards = more throughput. This demo runs with 2 shards by
default; you'll see `-s0` and `-s1` suffixes on container names throughout
(shard 0, shard 1).

That's the whole mental model. Everything else in the other docs is detail
on top of these six ideas (feed → tp → RDB/WDB → HDB, fronted by the
gateway, split across shards).

## Before you start: what you need

- **Docker and Docker Compose**, installed and running. If `docker compose
  version` prints a version number, you're set. (Docker Desktop on Mac/
  Windows includes both automatically.)
- **git**, to clone the repository.
- **A terminal.** Every command below is copy-pasteable as-is.
- **A free KDB-X licence.** kdb+/q itself is proprietary — this repository
  never contains the actual database engine or a licence file, only the
  code that runs on top of it. You need to get both yourself, once, for
  free:
  1. Go to the [KX Developer Center](https://kx.com/developers/) and
     register for the free **KDB-X Community Edition**. This is genuinely
     free for this kind of use, no credit card.
  2. Download the Linux build (even if your laptop is Mac/Windows — the
     database runs *inside* the Docker containers, which are Linux) and
     your licence file.
  3. You'll get two files: the `q` binary itself, and a licence file. KX
     sometimes names the licence file something other than `kc.lic` — this
     platform always expects it named exactly `kc.lic`, so rename it if
     needed. Keep both — you'll place them in step 3 below.

You do **not** need q/kdb+ installed directly on your laptop, and you do
**not** need Python or Node.js installed either — everything runs inside
Docker containers that already have what they need.

## Step 1 — Get the code

```bash
git clone <this repository's URL>
cd kdb-distributed-controlplane
```

(If you already have it locally, just `cd` into it.)

## Step 2 — Create your configuration file

Every setting — passwords, secrets, feed rates, feature toggles — lives in
one file, `.env`, which you create by copying the template:

```bash
cp .env.example .env
```

Open `.env` in any text editor. For a first run on your own laptop, you only
need to touch two things (everything else has a working default):

1. **`ADMIN_PASSWORD_HASH`** — leave it blank and the system falls back to
   the built-in demo password `changeme`. That's fine for now; **don't**
   leave it blank if you ever expose this beyond your own machine (see
   [README.md](README.md)'s secret-rotation checklist before doing that).
2. **`DEPLOYMENT_ENV`** — leave it as `local`. This is a real setting that
   controls whether a product licence key is *required* to start: `local`
   (the default) means no — this is your own laptop, run it freely. Setting
   this to `customer` (which the deploy scripts for a real customer box do
   automatically) makes a valid `LICENSE_KEY` mandatory. You don't need one
   for this guide.

Everything else — `JWT_SECRET`, `WATCHDOG_SHARED_SECRET`, and so on — has a
working (if insecure) default for local use. The file itself explains what
each setting does; you'll come back and rotate the real secrets later if
this ever needs to run somewhere other than your own machine.

## Step 3 — Place the KDB-X binary and licence

Put the two files from the prerequisites (the `q` binary and your licence
file) here:

```
data-plane/docker/kdbx/
```

The exact expected layout (per-architecture subfolders, licence filename) is
explained in that folder — if it's empty, create `data-plane/docker/kdbx/`
first. This step is the one people most often get wrong: if the containers
in step 4 keep restarting, come back and re-check this step before anything
else (see [troubleshooting.md](troubleshooting.md)).

## Step 4 — Build and start everything

```bash
docker compose build
docker compose up -d
```

The first `build` genuinely takes a few minutes — it's compiling/installing
everything from scratch. `up -d` starts every container in the background
(`-d` = detached, so it doesn't hold your terminal hostage).

## Step 5 — Verify it actually came up

Don't just trust that the commands above didn't print an error — actually
check:

```bash
docker compose ps
```

You should see a list of ~15-20 containers (two of everything for shard 0
and shard 1, plus one gateway, one control-api, one web-ui, one watchdog),
every one of them showing `Up` in its status column, something like:

```
NAME                                 STATUS
kdb-control-plane-control-api-1      Up 30 seconds
kdb-control-plane-gateway-1          Up 32 seconds
kdb-control-plane-rdb-s0-1           Up 33 seconds
kdb-control-plane-tp-s0-1            Up 34 seconds
kdb-control-plane-web-ui-1           Up 30 seconds
...
```

If anything shows `Restarting` or is missing entirely, **stop here** and go
to [troubleshooting.md](troubleshooting.md) rather than continuing — the
most common cause at this exact point is step 3 (the KDB-X binary/licence)
not being where the containers expect it.

Two more direct checks:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/health
# expect: 200

curl -s -o /dev/null -w "%{http_code}\n" http://localhost/
# expect: 200
```

`8000` is the API talking directly; plain `http://localhost/` is the web UI
(which itself talks to the API behind the scenes — you're checking that the
whole chain, not just one piece, is reachable).

## Step 6 — Log in

Open `http://localhost/` in a browser. Two accounts are seeded automatically
on first boot:

| Role | Email | Password |
|---|---|---|
| Demo tenant admin (day-to-day use) | `admin@demo-bank.local` | `changeme` |
| Platform admin (cross-tenant/fleet management) | `admin@platform.local` | `changeme` |

(Both passwords are `changeme` because `ADMIN_PASSWORD_HASH` was left blank
in step 2 — see that step's warning about not doing this beyond your own
laptop.)

Log in as the demo tenant admin first — that's the account that sees the
day-to-day trading/query/monitoring screens or a real operator would use.

## Step 7 — Make something actually happen

Fresh out of the box, the dashboard (the **Overview** page, which is where
you land after login) will honestly tell you: tickerplants are up, but
nothing is publishing data yet. That's expected — a real market-data
connection is something you turn on deliberately, not something that starts
itself.

1. Click **Connectors** in the left sidebar.
2. Find **bpipe-sim** (a *simulated* equities feed — it invents
   realistic-looking fake trades, useful for exactly this: seeing the whole
   pipeline work end to end with zero external dependencies) and toggle it
   on.
3. Go back to **Overview**. Within a few seconds you should see: the
   "Throughput" number climb off zero, the live ingest chart start filling
   in, and the "● LIVE" badge (top right) turn on.

You just watched a trade get generated, hit the tickerplant, get logged,
get relayed to the RDB, and show up in a query — the entire pipeline from
the "five pieces" section above, actually running.

4. Click **Query** in the sidebar, and run:
   ```
   select from trade
   ```
   then press **Run**. You'll get back real rows — whatever `bpipe-sim` has
   generated so far — with columns like `time`, `sym`, `price`, `size`. This
   is a live query against the RDB you just read about, running for real,
   right now.

## Step 8 — A five-minute tour

- **Topology** — every managed process (tp/RDB/WDB/HDB per shard, plus the
  gateway), with start/stop/restart controls. Try stopping one — watch the
  **watchdog** notice and restart it automatically within a few seconds
  (this is the "self-healing" the README talks about). Check the **Audit
  log** page afterward to see it logged what it did and why.
- **Markets** — a live price/volume view per symbol, backed by the same
  query path you just used manually.
- **Bot** — a paper-trading momentum strategy that runs server-side (not in
  your browser) against the live feed. Entirely simulated money — see
  [platform-usage.md](platform-usage.md) for the honest details on what's
  real vs. simulated here.
- **Metrics** — the detailed, per-shard version of Overview's headline
  numbers, including pipeline latency broken down by stage.

## What's next

- **Using the platform day to day**: [platform-usage.md](platform-usage.md)
  — every page, what it actually does.
- **Operating the tick chain** (retention, sharding, thread sizing, the
  watchdog's actual runbooks): [tickerplant-administration.md](tickerplant-administration.md).
- **Something's broken**: [troubleshooting.md](troubleshooting.md) — real
  incidents this exact deployment has hit, organized by symptom.
- **Changing the code**: [developer-guide.md](developer-guide.md) — repo
  layout, where to make a given kind of change, testing conventions.
- **Deploying somewhere real** (a cloud VM for a demo, Kubernetes for a
  pilot, or the real multi-tenant hosted path): [deployment-process.md](deployment-process.md)
  — but read that guide's own pre-deployment checklist first; the defaults
  you used in this guide (blank admin password, demo secrets,
  `DEPLOYMENT_ENV=local`) are deliberately insecure conveniences for a
  laptop, not something to carry forward.

## Shutting it down

```bash
docker compose down          # stops and removes containers, keeps your data volumes
docker compose down -v       # also wipes the data volumes - a genuinely fresh start next time
```
