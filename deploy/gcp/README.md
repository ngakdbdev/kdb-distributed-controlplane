# Deploying to GCP

Mirrors the AWS and Azure modules: provision → networking → docker → deploy
→ teardown.

This is the **single-VM, throwaway, one-prospect-demo path** — not
production-grade (no HA, blast radius is the whole box). If you're taking
this to a real customer beyond a one-off demo, read
[docs/getting-started.md](../../docs/getting-started.md)'s hardening
pointers and [docs/deployment-process.md](../../docs/deployment-process.md)
first — this README covers *provisioning*, not everything you should do
before something is customer-facing.

## Before you start

1. Install the `gcloud` CLI and run `gcloud auth login`. Confirm it works:
   `gcloud config list` should show your account, not an error.
2. Have a GCP project with billing enabled (the $300/90-day trial credit
   covers this entire deployment).
3. Pick a zone: `export GCP_ZONE=us-central1-a` (or nearer your audience).
   Not every machine type is available in every zone — if step 1 below
   fails on this, the error tells you exactly how to check and what to
   override.
4. Download **KDB-X Community Edition** (free, no expiry, commercial use
   allowed) from the KX Developer Center: https://kx.com/products/kdb-x/ —
   grab the Linux binary and your `kc.lic` license file. This repo cannot
   bundle them for you; KX's terms require you to obtain them directly.

## Steps

```bash
export GCP_PROJECT_ID=your-project-id
export GCP_ZONE=us-central1-a          # pick a zone close to your demo audience

# 1. Provision the VM (C3 machine type, Tier_1 networking, compact placement)
./01_provision_vm.sh
#    -> prints the VM's external IP
#
#    If this fails with a "machine type is not offered in zone" error, the
#    script's own output tells you exactly how to check which zones do
#    have it, and gives you a MACHINE_TYPE= override - re-run with it set.
#    This is the single most common failure point of this whole path,
#    almost always caused by C3's narrower zone coverage vs. general-
#    purpose families, not a real problem with your account.

# 2. Open the firewall for SSH / HTTP / the control API
./02_configure_networking.sh
#    By default this opens SSH (22) and the control-api debug port (8000)
#    to the whole internet (0.0.0.0/0) - fine for a five-minute throwaway
#    demo, NOT fine for anything left running. Set ALLOWED_SSH_CIDR and
#    ALLOWED_ADMIN_CIDR to your own IP before running this for anything
#    you'll leave up:
#      export ALLOWED_SSH_CIDR="$(curl -s ifconfig.me)/32"
#      export ALLOWED_ADMIN_CIDR="$(curl -s ifconfig.me)/32"

# 3. Verify the VM is actually reachable before continuing
gcloud compute ssh kdb-control-plane-demo --zone "$GCP_ZONE" --command "echo connected"
#    If this hangs or refuses: check step 2's firewall rules actually allow
#    your current IP, and that the VM shows RUNNING in the console.

# 4. On the VM: get the code and install Docker
gcloud compute ssh kdb-control-plane-demo --zone "$GCP_ZONE"
#   ...now on the VM:
git clone <this-repository-url>
cd kdb-distributed-controlplane
bash deploy/gcp/03_install_docker.sh
#    Read its final output carefully - it tells you exactly how to get a
#    shell with docker-group access before continuing (either reconnect
#    SSH, or run `newgrp docker`). Skipping this is the second most common
#    failure point: step 6 below fails with a permission error that has
#    nothing to do with anything else if you don't do this first.

# 5. Place your KDB-X binary + licence
mkdir -p data-plane/docker/kdbx
#    copy your downloaded `q` binary and `kc.lic` into
#    data-plane/docker/kdbx/ now (scp from your laptop, or download
#    directly on the VM)

# 6. Deploy
bash deploy/gcp/04_deploy_stack.sh
#    If this refuses immediately with "cannot talk to the Docker daemon",
#    that's step 4's docker-group timing issue - see its own error message.
#    First run also copies .env.example to .env and stops so you can edit
#    it - see docs/getting-started.md step 2, and set LICENSE_KEY /
#    DEPLOYMENT_ENV as this script's own reminder says (this is a
#    customer-facing path, DEPLOYMENT_ENV is set to "customer"
#    automatically, which makes a valid LICENSE_KEY mandatory).
```

**Verify it's actually up** before telling anyone the URL:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://<vm-external-ip>:8000/health   # expect 200
curl -s -o /dev/null -w "%{http_code}\n" http://<vm-external-ip>/              # expect 200
```

Open `http://<vm-external-ip>/` for the UI and log in. Enable the `bpipe-sim` and `crims-sim` connectors
from the Connectors tab to start generating traffic, then watch the Metrics tab fill in.

## The self-healing demo moment

On the Topology tab, click **"Kill (demo self-heal)"** next to any process (e.g. `rdb-a-m`). Within a
few seconds the Watchdog will detect it's down and restart it automatically - watch the status badge flip
red then back to green, then check the Audit log tab for the `detect_failure` → `auto_heal` trail.

## Troubleshooting provisioning specifically

- **"machine type ... is not offered in zone"** — `01_provision_vm.sh`
  already checks this before attempting creation and tells you how to find
  a zone that does have it, or set `MACHINE_TYPE=` to something the zone
  does offer.
- **SSH hangs / connection refused** — almost always step 2's firewall
  rules not actually allowing your current IP, especially if
  `ALLOWED_SSH_CIDR` was set to a value that's since changed. Re-run
  `./02_configure_networking.sh` with the current value.
- **`04_deploy_stack.sh` fails with a Docker permission error** — step 4's
  docker-group timing issue; see that step's note above.
- **Containers keep restarting after `04_deploy_stack.sh`** — almost always
  the KDB-X binary/licence (step 5) not actually being at
  `data-plane/docker/kdbx/`, or being the wrong architecture. See
  `docs/troubleshooting.md`.

## Deploying on a free-tier / brand-new GCP project

`01_provision_vm.sh` checks this automatically, with a real quota lookup
(not a guess): if no `MACHINE_TYPE` is set and your project's regional CPU
quota is too low for the default `c3-standard-8`, it falls back on its own
to `e2-micro` (GCP's actual Always Free machine type) with a 30GB
pd-standard disk, and skips the COMPACT placement policy + Tier_1/gVNIC
networking (e2-micro doesn't support gVNIC, and neither is useful with one
box). It also warns (doesn't block) if your zone isn't in one of GCP's
genuinely-free regions (`us-west1`/`us-central1`/`us-east1`) - e2-micro
runs fine elsewhere, it just isn't $0 there. You'll see a message
explaining what it did and why.

Already know you're on a free-tier project? Skip straight there:
```
FREE_TIER=1 ./01_provision_vm.sh
```

`04_deploy_stack.sh` (step 4) does the matching check on the stack side -
it reads the box's own actual RAM (not a cloud API - works the same however
the box was created) and, below ~3.5GB, regenerates `docker-compose.yml`
for 1 shard instead of 2 and turns off the `ollama` service (NL2Q's
natural-language-to-q box, ~2.4GB RAM held permanently) so the rest of the
stack actually fits. Force it either way with `FREE_TIER=1`/`FREE_TIER=0`.
See `deploy/lib/free_tier.sh` for exactly what it changes.

## Honest note on "FPGA" and "highest throughput"

GCP does not offer an FPGA instance family - that's AWS F1/F2 territory. This deployment uses GCP's real
low-latency levers instead: a compute-optimized **C3** machine type, **Tier_1** networking (requires
gVNIC, doubles per-VM network bandwidth), and a **COMPACT placement policy** (matters once you split
this onto multiple VMs for real multi-node testing - a single VM has no inter-VM hops to save). If a
prospective client specifically needs FPGA-accelerated execution, that is a separate AWS or on-prem/colo
workstream - say so plainly rather than imply GCP can do it.

## Tearing down

```bash
export GCP_PROJECT_ID=your-project-id
export GCP_ZONE=us-central1-a
./99_teardown.sh
```

Run this as soon as you're done demoing - the VM otherwise keeps burning trial credit and, eventually,
real money. `99_teardown.sh` deletes the VM, its static IP, the placement policy, and the firewall rules.
