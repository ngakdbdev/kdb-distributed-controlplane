# Hardening checklist

Everything in this doc is something the *demo* defaults deliberately get
wrong, on purpose, in favor of "works with zero configuration." Every item
below is a real, specific gap in this codebase today — not generic security
advice — with the exact file/setting to change and why it matters. Work
through this before anything is customer-facing, not after.

This is written so you (or a customer's own ops/security team) can harden a
deployment with **no help from the product team** — every item names the
exact env var, file, or command, not just "secure this."

## How to use this doc

Read it top to bottom once before your first real (non-laptop) deployment.
After that, treat it as a checklist: copy the table at the bottom, tick off
each row, and keep the filled-in copy as your record of what was actually
done for a given deployment (useful for a security review, or for yourself
in six months).

## 1. Secrets — rotate every one of these

`.env.example` is meant to be copied and edited, **not deployed as-is**.
Several values in it look like real credentials, not obvious placeholders
(`FINNHUB_API_KEY`, `TWELVEDATA_API_KEY`, `KX_BEARER_TOKEN`) — treat every
value that has ever been in that file's git history as untrusted, even if
you're rotating it "just to be safe."

| Setting | Where | Why |
|---|---|---|
| `JWT_SECRET` | `.env` | Signs every login session token. If this is guessable/shared, anyone can forge a valid session for any user. Generate: `openssl rand -hex 32`. Never reuse the literal placeholder text. |
| `WATCHDOG_SHARED_SECRET` | `.env` | Authenticates the watchdog's internal calls to `/audit/internal`. A leaked value lets anything on the network write fake audit events. Generate the same way, a *different* random value than `JWT_SECRET`. |
| `ADMIN_PASSWORD_HASH` | `.env` | Blank falls back to the built-in demo password (`changeme`) for **every** seeded admin account. Generate a real bcrypt hash: `python3 -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"`. |
| `LICENSE_SIGNING_SECRET` | `.env` (control-api) | Signs product license keys (`control-api/app/licensing.py`). The built-in default is a well-known string checked into this repo's source — if you leave it, anyone who has read this repo can mint themselves a valid license key. Set a real secret before relying on `DEPLOYMENT_ENV=customer` enforcement for anything that actually needs to be enforced against a determined party, not just accidental misconfiguration. |
| `KX_BEARER_TOKEN` | `.env` | Every kdb+ container pulls its own binary from the KX portal at start using this token - there's no local-binary alternative anymore. Rotate it in the KX portal if you ever used a value that matches (or was derived from) what's in this repo's history - a real KX license and binary were committed to this repo's git history at one point (now removed from tracking, but still present in past commits/any remote that ever received them); rotate your real license/token with KX if this repo was ever pushed anywhere. |
| `FINNHUB_API_KEY` / `TWELVEDATA_API_KEY` | `.env` | Optional live market-data connectors, off by default. Don't carry forward whatever's in `.env.example` — issue your own keys, or drop these entirely if you're not using those connectors. |
| Any provider API key you add later | `.env` | Same rule: never commit a real value, always issue your own. |

**Confirm `.env` itself is gitignored** (it is, by default) and that nobody
has force-added it in a fork: `git check-ignore .env` should print `.env`,
not nothing.

## 2. Network exposure — close what shouldn't be public

By default, the cloud VM deploy scripts (`deploy/aws|gcp|azure`) open two
things to the entire internet unless you override them:

- **SSH (port 22)** — set `ALLOWED_SSH_CIDR` to your own IP
  (`$(curl -s ifconfig.me)/32`) before running `02_configure_networking.sh`,
  not after. If you already ran it with the default, re-run it — the
  scripts are idempotent and update the existing rule.
- **The control-api debug port (8000)** — this is the API with **no**
  reverse proxy or TLS in front of it, meant for troubleshooting during a
  demo. Set `ALLOWED_ADMIN_CIDR` the same way, and consider not opening it
  at all for anything beyond active debugging — everything the UI needs
  goes through port 80/443 already.

**The only ports that should ever be reachable from the public internet on
a real deployment are 80 and 443** (and only 443 once TLS is set up — see
§3). Every kdb+ process (tickerplant, RDB, WDB, HDB, gateway — ports
5010-5060) stays on the internal Docker network by design; confirm this
wasn't changed with `docker compose ps` and checking no `50XX` port shows a
host-side binding.

## 3. TLS — don't run a real deployment on plain HTTP

The default `docker compose up` serves plain HTTP on port 80. For anything
beyond your own laptop, use the TLS overlay (`deploy/tls/`) — see
[deployment-process.md](deployment-process.md) §2 for the exact commands.
Two variants:

- **Local/internal testing** (`docker-compose.local-tls.yml`) — self-signed
  cert, fine for testing the HTTPS *path* works, not for anything a real
  user's browser should trust without a manual override.
- **Real deployment** (`docker-compose.tls.yml`) — genuine Let's Encrypt
  cert, auto-renewing. This is the one to actually use once `TLS_DOMAIN`
  points at a real, DNS-registered domain.

Once TLS is live, **stop publishing the plain-HTTP web-ui port and the
control-api debug port directly** — the TLS overlay already does this for
web-ui (`ports: !reset []`); make sure you're not *also* separately exposing
port 8000 per §2.

## 4. Database — SQLite is a demo default, not a production one

`DATABASE_URL` defaults to a local SQLite file. SQLite has **no
concurrent-writer safety and no HA story** — fine for one person kicking
the tires, wrong for anything two people might use at once, and wrong for
anything that needs to survive an instance restart without you personally
babysitting a file. Point `DATABASE_URL` at a real Postgres/MySQL/MSSQL
instance (managed — RDS/Cloud SQL/Azure Database — strongly preferred over
self-hosting one more thing to operate) before this is customer-facing. See
`control-api/README-database.md` for exact connection string formats per
dialect and the driver each one needs.

The Helm chart (the pilot/production Kubernetes path) **hard-fails**
`helm install`/`upgrade` if you enable `controlApi.autoscaling` against a
`sqlite://` URL — that guard exists because it's trivially easy to demo,
silently corrupt under two concurrent writers, and only notice in front of
a client. There's no equivalent guard on the single-VM path; that's on you.

## 5. Licensing — make sure enforcement is actually on

A product license key is meant to be **mandatory** for any deployment that
isn't your own laptop (`control-api/app/licensing.py`). Confirm this is
actually true for what you're deploying:

```bash
# On the deployed box, check what the running container actually has:
docker compose exec control-api printenv DEPLOYMENT_ENV
# expect: customer  (for anything customer-facing)
```

- The cloud VM deploy scripts set `DEPLOYMENT_ENV=customer` in the `.env`
  they generate automatically — confirm it's still there if you hand-edited
  `.env` afterward.
- The Helm chart defaults `licensing.deploymentEnv` to `"customer"` — only
  override to `"local"` for a deliberate internal test cluster, never for
  anything a customer will see.
- `fleet_agent` forces `DEPLOYMENT_ENV=customer` itself, regardless of what
  a tenant's own `.env` says, since running there means it's a customer's
  environment by definition — no action needed, but worth knowing it's not
  relying on the tenant getting a config value right.
- If enforcement is on and the box refuses to start, that's it working
  correctly, not a bug — set a real `LICENSE_KEY`.

## 6. CORS — the API accepts requests from any origin by default

`control-api/app/main.py` sets `allow_origins=["*"]` (every origin allowed)
with an explicit comment that this needs tightening beyond a local demo.
For a real deployment, change this to the exact origin(s) your web UI is
actually served from (e.g. `["https://vantik.yourcompany.com"]`) — a
wildcard origin combined with credentialed requests is a real
cross-origin-attack surface, not just a lint warning.

## 7. Query workspace — writes are opt-in, keep it that way unless you mean it

The live query workspace defaults to **read-only** (`QUERY_ALLOW_WRITE`
unset/false) — a denylist blocks the obvious escapes (`system`, `hopen`,
file/socket primitives, `set`/`upsert`, …) as defense in depth. Read
`control-api/app/query_service.py`'s own module docstring before ever
setting `QUERY_ALLOW_WRITE=1`: the denylist is **not a sandbox**, q itself
is not sandboxable the way SQL is, and the real boundary is operational —
point the query workspace at a restricted, read-only kdb+ process for
anything beyond a demo, never a write-capable production process.

## 8. The Docker socket — control-api and watchdog can control the whole host

Both `control-api` and `watchdog` mount `/var/run/docker.sock` into their
containers (see `docker-compose.yml`) so they can start/stop/restart the
other containers (the Topology page, self-healing). **This is effectively
root on the host** — anything that can reach that socket can run arbitrary
containers, not just the ones this product manages. Consequences:

- Never expose control-api's debug port (8000) publicly (§2) — a
  vulnerability in the API is a vulnerability in the whole host if it can
  be reached and exploited.
- On Kubernetes (the Helm path), the equivalent privilege is the
  `ORCHESTRATOR_BACKEND=kubernetes` service account's RBAC permissions
  (`helm/kdb-control-plane/templates/`) — review what it's actually scoped
  to (should be namespace-scoped pod/deployment control, not
  cluster-admin) before granting it in a shared cluster.
- If your organization's policy prohibits mounting the Docker socket into
  application containers at all, the self-healing/topology-control feature
  needs an alternative (a privilege-separated sidecar, or accepting
  manual-only recovery) — that's a real architecture change, not a config
  flag, and worth raising with whoever owns that policy before deploying
  here rather than after.

## 9. Authentication — move off local passwords for real users

The seeded demo accounts (`admin@platform.local` / `admin@demo-bank.local`,
both defaulting to `changeme`) are for kicking the tires, not for real
users. Two real options, both already wired in:

- **SSO (Microsoft Entra / OIDC)** — per-tenant, configured via the
  `TenantIdP` model / `/auth/sso` routes. Users are provisioned
  just-in-time on first successful login; role comes from their Entra
  group/app-role membership.
- **LDAP / on-prem Active Directory** — per-tenant, via `TenantLDAP` /
  `/auth/ldap` routes, for customers who authenticate against their own
  directory instead of a cloud IdP.

Keep local password auth only for a platform-admin break-glass account
(and rotate `ADMIN_PASSWORD_HASH` per §1), not for every real user.

## 10. Monitoring what you just hardened

- **Audit log** (`/audit` page, `AuditEvent` table) — every admin action
  and notable system event (service start/stop, connector toggles, order
  placement, provisioning). Check this after any change from this
  checklist to confirm it actually took effect and was logged.
- **Watchdog** — confirm it's actually running (`docker compose ps
  watchdog`) and check its logs for repeated `escalate`/`oom_crash_loop`
  entries, which mean something is chronically failing, not self-healing —
  see `docs/troubleshooting.md`.

## What this checklist does *not* cover

Said plainly, so nobody assumes otherwise:

- **Backups.** Nothing in this repo backs up the control-plane database,
  the kdb+ historical database (HDB), or the tickerplant logs
  automatically. That's infrastructure you own on top of whichever cloud
  you picked (snapshot policies, `pg_dump` crontabs, HDB replication to
  object storage).
- **Multi-region / DR.** Everything here is single-region, single-AZ by
  default.
- **Penetration testing / formal security review.** This checklist closes
  the *known, specific* gaps in this codebase's own defaults. It is not a
  substitute for an actual security assessment before handling real
  customer data or real trading activity.
- **Compliance certification** (SOC 2, ISO 27001, etc.) — none of this
  repo's own tooling produces or manages compliance evidence.

## The checklist, copy-paste this table

```
[ ] Rotated JWT_SECRET, WATCHDOG_SHARED_SECRET, ADMIN_PASSWORD_HASH, LICENSE_SIGNING_SECRET
[ ] Rotated/dropped any provider API keys inherited from .env.example
[ ] Confirmed .env is gitignored and not force-added anywhere
[ ] ALLOWED_SSH_CIDR and ALLOWED_ADMIN_CIDR set to real, narrow ranges (not 0.0.0.0/0)
[ ] Only 80/443 reachable from the public internet - no 5010-5060, no 8000
[ ] TLS overlay in use (deploy/tls/docker-compose.tls.yml) with a real cert, not plain HTTP
[ ] DATABASE_URL points at a real Postgres/MySQL/MSSQL, not sqlite://
[ ] DEPLOYMENT_ENV=customer confirmed on the running container, with a real LICENSE_KEY
[ ] CORS allow_origins tightened to the real UI origin(s), not "*"
[ ] QUERY_ALLOW_WRITE left off, or the query workspace pointed at a restricted read-only process
[ ] Docker-socket-holding services (control-api, watchdog) confirmed not reachable except internally
[ ] Real users authenticate via SSO/LDAP, not the seeded local demo accounts
[ ] A backup strategy exists for the control-plane DB and the HDB (owned by you, not this repo)
```
