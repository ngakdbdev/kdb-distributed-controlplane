# Deployment process

The actual steps to stand this up, for each of the four paths. Read
`docs/README.md`'s pre-deployment checklist **first** - secrets to rotate,
decisions to make before you run anything below. This guide picks up where
that one leaves off: the commands themselves, in order, plus what "done"
looks like at each step.

| Path | Use case | Guide below |
|---|---|---|
| Local docker-compose | Development, this-machine demo | §1 |
| Local docker-compose + TLS | A demo you'll share a real URL for | §2 |
| Single-VM cloud (AWS/GCP/Azure) | One-prospect sales demo, throwaway | §3 |
| Kubernetes (Helm) | Pilot / longer-lived single-tenant deployment | §4 |
| Fleet (multi-tenant) | Real hosted SaaS - agent runs in the *tenant's* cluster | §5 |

## 1. Local docker-compose

```bash
cp .env.example .env
# edit .env: rotate JWT_SECRET/WATCHDOG_SHARED_SECRET, set ADMIN_PASSWORD_HASH,
# stage KX binaries (see docs/README.md's shared checklist)

python3 scripts/gen_topology.py --shards 2 --compose docker-compose.yml \
  --shards-json data-plane/shards.json --eod-hour 0 --idb-retention-days 5

docker compose up -d --build
```

**Verify it's actually up**, don't just trust exit codes:
```bash
docker compose ps                                    # everything "Up", low RestartCount
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/query/targets   # 401 (needs auth) = control-api alive
curl -s -o /dev/null -w "%{http_code}\n" http://localhost/                     # 200 = web-ui alive
```

Log in at `http://localhost` with the demo tenant admin
(`DEMO_TENANT_ADMIN_EMAIL`, default `admin@demo-bank.local`) or the
platform admin (`PLATFORM_ADMIN_EMAIL`).

**Changing shard count later**: re-run the `gen_topology.py` command with a
new `--shards N`, then `docker compose up -d --remove-orphans` (the
`--remove-orphans` matters - a shrink leaves the old shard's containers
defined nowhere in the new file, and without that flag they keep running
orphaned).

## 2. Local docker-compose + TLS (Caddy)

Same as §1, plus a TLS overlay that puts Caddy in front as the sole public
entrypoint. Two overlay variants:

- `deploy/tls/docker-compose.local-tls.yml` - self-signed cert from Caddy's
  own internal CA. Use when the domain resolves to your own machine (a
  hosts-file entry) and there's no public DNS/reachability for a real cert.
  The browser will warn the cert isn't trusted; either click through each
  time or trust Caddy's local CA machine-wide (see
  `docs/troubleshooting.md`'s WebSocket/offline-dashboard entry - the
  click-through does *not* reliably cover WebSocket connections, only the
  page itself).
- `deploy/tls/docker-compose.tls.yml` - real Let's Encrypt cert. Needs a
  public DNS A record pointing at this host and ports 80+443 reachable from
  the internet.

```bash
# set TICKFORGE_DOMAIN (+ ACME_EMAIL for the real-cert variant) in .env

docker compose -f docker-compose.yml -f deploy/tls/docker-compose.local-tls.yml up -d --build
```

**Every subsequent command targeting this stack needs both `-f` flags
together** - a bare `docker compose up -d <service>` re-publishes that
service's plain-HTTP port directly, colliding with Caddy already holding
it (see `docs/troubleshooting.md`). Consider exporting a shell alias for
the session:

```bash
alias dc-tls='docker compose -f docker-compose.yml -f deploy/tls/docker-compose.local-tls.yml'
dc-tls up -d control-api   # etc.
```

**Verify**: `https://$TICKFORGE_DOMAIN/` loads, and the WebSocket-backed
live dashboard actually shows "● LIVE" (not just that the page renders -
see the troubleshooting guide if it's stuck on "○ offline").

## 3. Single-VM cloud (AWS / GCP / Azure)

One numbered script per stage, mirrored across all three clouds
(`deploy/<cloud>/`). Read that cloud's `README.md` for the exact
prerequisites (CLI auth, permissions needed) before starting - summarized:

```bash
export AWS_REGION=us-east-1          # or the equivalent for gcp/azure
export VM_NAME=kdb-control-plane-demo

cd deploy/aws        # or gcp / azure
./02_configure_networking.sh     # security group / firewall first
./01_provision_vm.sh             # compute-optimized instance, prints public IP
./03_install_docker.sh           # docker + compose plugin on the fresh VM
./04_deploy_stack.sh             # clones/copies the repo, runs docker compose up
```

This is the **single-VM, throwaway, one-prospect-demo path** - not
production-grade (no HA, blast radius is the whole box). `99_teardown.sh`
tears it back down; run it when the demo's over rather than leaving a
billable VM running.

**Verify**: SSH in and repeat §1's verification commands, or just hit the
public IP/domain from a browser.

## 4. Kubernetes (Helm) - the pilot/production path

```bash
helm install demo ./helm/kdb-control-plane \
  -f helm/kdb-control-plane/values-aws.yaml \
  --set secrets.jwtSecret=$(openssl rand -hex 32) \
  --set secrets.watchdogSharedSecret=$(openssl rand -hex 32) \
  --namespace kdb-control-plane --create-namespace
```

Substitute `values-gcp.yaml` / `values-azure.yaml` for the other clouds, or
hand-roll your own values file for on-prem/k3s. Key knobs (see
`values.yaml`'s own comments for the full list):

- `shardCount` - the single scaling dimension; every per-shard resource,
  the gateway's routing ConfigMap, and the feed fan-out all derive from
  this one number.
- `eod.hourUtc`, `idb.retentionDays`, `rdb.retentionMin`,
  `hdb.retentionDays` - see `docs/tickerplant-administration.md`'s
  retention section for what each actually bounds.
- `resources.<component>` / `nodePools.<component>` - per-tier CPU/memory/
  storage/instance-type, auto-derived from a TickHouse spec's `profile`
  when provisioned declaratively (§5), or set directly here for a
  Helm-only install.

**Before deploying for real**, verify the chart renders correctly for your
shard count - catches template errors without touching a cluster:
```bash
helm template t ./helm/kdb-control-plane --set shardCount=3 > /dev/null && echo OK
```

**Verify after install**:
```bash
kubectl get pods -n kdb-control-plane            # everything Running, low restart counts
kubectl logs -n kdb-control-plane deploy/watchdog --tail 20   # no active flap-loop warnings
```

**Scaling shard count later**: `helm upgrade` with a new `--set
shardCount=N` - same caveat as the compose path, a new shard starts empty,
this is a real topology change, not free/instant.

**Multi-region**: not supported by this chart today - one Helm release
targets one cluster/region. A real multi-region deployment means separate
releases with no built-in federation between them.

## 5. Fleet - real multi-tenant hosted SaaS

For the actual product shape (many tenants, control plane with no direct
network path into any of their environments): the control plane never
touches a tenant's kdb+ processes directly. Instead, a `fleet_agent`
process runs *inside* the tenant's own cluster/VM, enrolls with the control
plane via a one-time token, and polls for commands.

```bash
# 1. Platform admin creates the tenant + agent record (via the Fleet page,
#    or POST /fleet/agents) - this mints a one-time enrollment token, shown
#    once via the UI's "Register agent" button.

# 2. Inside the tenant's own environment - config is env vars, not flags:
export CONTROL_PLANE_URL=https://<your-domain>/api
export ENROLLMENT_TOKEN=<one-time token from step 1>
export AGENT_ENVIRONMENT=aws            # informational
export AGENT_BACKEND=helm               # or "compose" for on-prem/single-box
export HELM_RELEASE=kdb-control-plane   # helm backend only
export HELM_CHART=helm/kdb-control-plane
python -m fleet_agent
#    Enrolls once, then heartbeats on a loop, pulling queued
#    start/stop/restart/provision/deprovision commands.

# 3. Provision a TickHouse against that tenant from the control plane (UI
#    or POST /tickhouses + /tickhouses/{id}/provision) - the agent picks
#    up the desired shardCount and reconciles it locally: `helm upgrade
#    --install ... --set shardCount=N` (helm backend) or regenerates
#    docker-compose via gen_topology.py + `docker compose up -d` (compose
#    backend). Same single shardCount knob every other path in this guide
#    drives - not a parallel deployment mechanism.
```

**Verify**: the agent's heartbeat shows up as recent in the Fleet page, and
a provisioned TickHouse's component list matches what actually came up in
the tenant's environment (`fleet_agent/backends.py`'s `reconcile_spec` is
what translates the spec into the real `gen_topology.py`/`helm`
invocations - check the agent's own logs if something didn't land as
expected).

Full detail: `fleet_agent/README.md` - layout, what a provision command
actually does step by step, and `python -m pytest fleet_agent` (16 tests,
no live cluster needed) to verify the agent itself before trusting it
against a real tenant environment.
