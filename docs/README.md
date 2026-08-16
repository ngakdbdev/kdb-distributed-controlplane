# Documentation

## New to this? Start here.

[getting-started.md](getting-started.md) — the zero-to-running walkthrough.
Assumes nothing: explains what kdb+/a tickerplant/a shard even are in plain
English, then gets the whole system running on your own laptop step by
step, with expected output shown at each command. If this is your first
time in this codebase, read that one first, not the reference docs below.

## Building, running, and operating the platform

Reference material, not a tutorial — for once you're past the getting-started
guide and need the detail on a specific piece.

- [developer-guide.md](developer-guide.md) — codebase layout, where to make a
  given kind of change, local dev workflow, testing conventions
- [platform-usage.md](platform-usage.md) — every web UI page, what it
  actually does, and the real constraints behind each feature
- [tickerplant-administration.md](tickerplant-administration.md) — how the
  q/kdb+ tick chain works, sharding, retention/EOD, thread sizing, watchdog,
  common admin tasks
- [troubleshooting.md](troubleshooting.md) — real incidents this deployment
  has hit, organized by symptom, with the actual root cause and fix for each
- [deployment-process.md](deployment-process.md) — the actual steps to stand
  up each of the four deployment paths, in order, with what "done" looks
  like at each step

## Pre-deployment guides

Four guides, one per target, meant to be worked through **before** you run anything under
`deploy/<cloud>/` or `helm install`. Those existing scripts/charts answer "how do I stand this up";
these guides answer "what do I need to have ready and decided first, and what will bite me later if
I skip it." They're deliberately checklist-shaped, not tutorials — the quickstart commands still live
in each `deploy/<cloud>/README.md`, in `helm/kdb-control-plane/values.yaml`'s own comments, and now
in [deployment-process.md](deployment-process.md) above.

- [predeploy-aws.md](predeploy-aws.md) — single-VM demo on EC2
- [predeploy-gcp.md](predeploy-gcp.md) — single-VM demo on Compute Engine
- [predeploy-azure.md](predeploy-azure.md) — single-VM demo on an Azure VM
- [predeploy-kubernetes.md](predeploy-kubernetes.md) — EKS / AKS / GKE / on-prem via the Helm chart (the path to take once "demo" turns into "pilot" or a real, at-scale deployment) — see that guide's own section 0 if you don't have a cluster yet (`terraform/{aws,azure,gcp}/`)

## Which path

The single-VM `deploy/<cloud>/` scripts and the Kubernetes Helm chart are **not** two ways to do the
same thing — they're two different maturity stages of the same product, and the repo is honest about
that split (see the root `README.md`'s "what's built" section):

| | Single VM (`deploy/aws\|gcp\|azure/`) | Kubernetes (`helm/`), optionally cluster-provisioned by `terraform/` |
|---|---|---|
| Intended use | Sales demo, one prospect, throwaway | Pilot, longer-lived single-tenant deployment, or a real production/enterprise-scale environment |
| Cluster provisioning | N/A — one VM, no orchestrator | `terraform/{aws,azure,gcp}/` (VPC, managed Kubernetes, KMS-backed secrets encryption, standard + high-performance-filesystem storage tiers) if you don't already have a cluster — see each module's own README.md |
| Topology control | `/topology` router talks to the Docker socket directly | Same router talks to the Kubernetes API instead — same UI, different orchestrator backend |
| Multi-tenant hosted SaaS path | Not this — that's the `/fleet` + `fleet_agent` path, out of scope for both guides above | Out of scope here too — `fleet_agent` runs *inside a tenant's own cluster*, invoked separately |
| Blast radius if it falls over | One VM, one prospect's demo | Depends on what else shares the cluster |
| Config drift risk | Low — one box, one `.env` | Higher — `values.yaml` vs. `existingSecret` vs. `--set` at install time; pick one pattern and stick to it |
| Monitoring | Ad hoc (`docker compose logs`, the app's own live UI) | Real GET /metrics + optional Prometheus/Grafana wiring — see predeploy-kubernetes.md's monitoring section |

If you're not sure which you're doing: if the next step after this is a sales call, use the VM path.
If the next step is "the client wants this running for their own team to poke at for a few weeks" (or
longer, at real scale), use Kubernetes and a real (non-SQLite) database from day one — retrofitting a
database migration under a live pilot is exactly the kind of unforced error these guides exist to
prevent.

## Shared checklist — do this before any of the four guides

These four items are identical regardless of target, so they live here once instead of four times.

### 1. Rotate every secret in `.env.example` — do not reuse it

`.env.example` in the repo root is meant to be *copied and edited*, not deployed as-is — but as
shipped it currently contains values for `FINNHUB_API_KEY`, `TWELVEDATA_API_KEY`, and
`KX_BEARER_TOKEN` that are **not obvious placeholders** (they look like real-format tokens, not
`your-key-here`). Treat every value in that file as untrusted the moment it left a private
environment and reached a git history, regardless of whether it's still active:

- **Generate fresh values** for `JWT_SECRET` and `WATCHDOG_SHARED_SECRET` — 64+ random characters,
  e.g. `openssl rand -hex 32`. Never reuse the placeholder text (`change-me-to-a-random-64-char-string`)
  literally, and never reuse a value that ever appeared in `.env.example` at any point in the repo's
  history.
- **Generate a real `ADMIN_PASSWORD_HASH`**: `python3 -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"`.
  Leaving it blank falls back to the built-in demo password (`changeme`) — fine for a laptop, not
  fine for anything with a public IP.
- **Rotate `KX_BEARER_TOKEN`** in the KX portal if you ever obtained one that matches (or was derived
  from) what's checked in, before using it to pull binaries for a real deployment.
- **Rotate or drop `FINNHUB_API_KEY` / `TWELVEDATA_API_KEY`** — these are optional (`docker compose
  --profile providers up`) real market-data connectors, off by default. Don't carry forward whatever
  is in `.env.example`; issue your own keys from each provider if you actually want live data.
- Confirm `.env` itself (not just `.env.example`) is in `.gitignore` — it is, by default — and that
  nobody has force-added it in a fork.

### 2. Decide your KX-X licensing path

The `q` binary and license are proprietary and never bundled in this repo (see root `README.md`).
Every path below needs one of:
- **Community Edition** (free, commercial use allowed) — download from the KX Developer Center, stage
  the binary + `kc.lic` yourself (`data-plane/docker/kdbx/` for the VM path, a Kubernetes `Secret` for
  the Helm path).
- **Portal pull at deploy time** — set `KX_INSTALL_SOURCE=kx-portal` and a rotated `KX_BEARER_TOKEN`;
  the containers fetch the binary from the KX portal on first start. Requires outbound internet from
  the box/cluster.
- **Air-gapped** — stage the binary via `KX_INSTALL_SOURCE=local` and `KX_BINARIES_DIR`; no portal
  call needed at all.

Pick one *before* provisioning — it changes whether the target needs outbound internet access to the
KX portal, which affects the network/firewall guidance in each guide below.

### 3. Decide your database from day one if this isn't a throwaway demo

SQLite (the default everywhere) has no concurrent-writer safety and no HA story — fine for a solo demo,
wrong for anything two people might use at once or that needs to survive a pod/instance restart
without you personally babysitting the file. See `control-api/README-database.md` for exact connection
strings. Each cloud guide below names that cloud's managed equivalent (RDS / Azure Database / Cloud
SQL). The Helm chart **hard-fails** `helm install`/`upgrade` if you enable `controlApi.autoscaling`
against a `sqlite://` URL — that guard exists because it's trivially easy to demo, silently corrupt
under two writers, and only notice in front of a client.

### 4. Decide DNS/TLS before you provision, not after

All three VM paths default to plain HTTP on port 80 with the control-api debug port (8000) open to
`ALLOWED_ADMIN_CIDR`. `deploy/tls/` adds a Caddy edge that terminates HTTPS with an auto-renewing
Let's Encrypt certificate — see `deploy/tls/README.md`. It needs a DNS **A record** pointing at the
box's public IP *before* Caddy can request a certificate, so if you want HTTPS on day one (you should,
for anything beyond a same-day demo), register the DNS record before you provision the instance, not
after. Kubernetes has the equivalent decision baked into `ingress.tls` in `values.yaml` — see
[predeploy-kubernetes.md](predeploy-kubernetes.md).

## What none of these guides cover (say so plainly, don't let a client assume otherwise)

- **Backups.** Nothing in this repo backs up the control-plane database, the kdb+ historical database
  (`hdb`), or the tickerplant logs automatically. That's infrastructure you own on top of whichever
  cloud you pick (snapshot policies, `pg_dump` crontabs, HDB replication to object storage).
- **Multi-region / DR.** Everything here is single-region, single-AZ by default.
- **The hosted multi-tenant SaaS control plane.** That's the `/fleet` + `fleet_agent` architecture —
  a tenant runs `fleet_agent` inside *their own* cluster and it heartbeats out; the control plane never
  holds their cloud credentials. None of these four guides provision a tenant's cluster for them; that's
  the tenant's own infra team's job, informed by [predeploy-kubernetes.md](predeploy-kubernetes.md) if
  they're on Kubernetes.
- **Throughput numbers.** Never quote a number you haven't measured on the target box with
  `demokit.load_test` — see `DEMO.md`.
