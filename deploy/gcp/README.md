# Deploying to GCP

## Before you start

1. Install the `gcloud` CLI and run `gcloud auth login`.
2. Have a GCP project with billing enabled (the $300/90-day trial credit covers this entire deployment).
3. Download **KDB-X Community Edition** (free, no expiry, commercial use allowed) from the KX Developer
   Center: https://kx.com/products/kdb-x/ - grab the Linux binary and your `k4.lic` license file.
   This repo cannot bundle them for you; KX's terms require you to obtain them directly.

## Steps

```bash
export GCP_PROJECT_ID=your-project-id
export GCP_ZONE=us-central1-a          # pick a region close to your demo audience

# 1. Provision the VM (C3 machine type, Tier_1 networking, compact placement)
./01_provision_vm.sh

# 2. Open the firewall for SSH / HTTP / the control API
./02_configure_networking.sh

# 3. SSH in and install Docker
gcloud compute ssh kdb-control-plane-demo --zone "$GCP_ZONE"
#   ...then on the VM:
curl -O https://raw.githubusercontent.com/<your-repo>/main/deploy/gcp/03_install_docker.sh
bash 03_install_docker.sh
newgrp docker

# 4. Get the project onto the VM (git clone your repo, or scp the zip, then:)
cd kdb-control-plane
mkdir -p data-plane/docker/kdbx
# copy your downloaded `q` binary and `k4.lic` into data-plane/docker/kdbx/
cp .env.example .env
#   edit .env: set ADMIN_PASSWORD_HASH, JWT_SECRET, WATCHDOG_SHARED_SECRET

# 5. Deploy
bash deploy/gcp/04_deploy_stack.sh
```

Open `http://<vm-external-ip>/` for the UI and log in. Enable the `bpipe-sim` and `crims-sim` connectors
from the Connectors tab to start generating traffic, then watch the Metrics tab fill in.

## The self-healing demo moment

On the Topology tab, click **"Kill (demo self-heal)"** next to any process (e.g. `rdb-a-m`). Within a
few seconds the Watchdog will detect it's down and restart it automatically - watch the status badge flip
red then back to green, then check the Audit log tab for the `detect_failure` → `auto_heal` trail.

## Honest note on "FPGA" and "highest throughput"

GCP does not offer an FPGA instance family - that's AWS F1 territory. This deployment uses GCP's real
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
real money.
