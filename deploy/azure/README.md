# Deploying to Azure

Mirrors the AWS and GCP modules: provision → networking → docker →
deploy → teardown. Uses Azure's real low-latency levers (accelerated
networking, proximity placement group, compute-optimized VM size). There is
**no FPGA option** here on purpose — see the note below.

This is the **single-VM, throwaway, one-prospect-demo path** — not
production-grade (no HA, blast radius is the whole box). If you're taking
this to a real customer beyond a one-off demo, read
[docs/getting-started.md](../../docs/getting-started.md)'s hardening
pointers and [docs/deployment-process.md](../../docs/deployment-process.md)
first — this README covers *provisioning*, not everything you should do
before something is customer-facing.

## Before you start

1. Install the Azure CLI and run `az login`. Confirm it works: `az account
   show` should print your subscription, not an error.
2. Pick a region: `export AZURE_LOCATION=eastus` (or nearer your audience).
   Not every VM size is available in every region — if step 1 below fails
   on this, the error tells you exactly how to check and what to override.
3. Download **KDB-X Community Edition** (free, commercial use allowed) from the
   KX Developer Center — the Linux `q` binary and your `kc.lic`. This repo can't
   bundle them; KX's terms require you to obtain them directly.

## Steps

```bash
export AZURE_LOCATION=eastus
export VM_NAME=kdb-control-plane-demo

# 1. Provision RG + VM (accelerated networking, proximity placement group)
./01_provision_vm.sh
#    -> prints the public IP and the ssh command
#
#    If this fails with a "VM size is not offered in location" error, the
#    script's own output tells you exactly how to check availability and
#    gives you a VM_SIZE= override - re-run with it set. This is the
#    single most common failure point of this whole path, almost always
#    caused by the chosen region not (yet) offering that accelerated-
#    networking-capable size, not a real problem with your account.

# 2. Open HTTP + the control-api port on the VM's NSG
./02_configure_networking.sh
#    SSH (22) is opened by `az vm create` itself in step 1, to the whole
#    internet by default. The control-api debug port (8000) is ALSO open
#    to the whole internet by default here - fine for a five-minute
#    throwaway demo, NOT fine for anything left running. Set
#    ALLOWED_SSH_CIDR and ALLOWED_ADMIN_CIDR to your own IP before running
#    this for anything you'll leave up:
#      export ALLOWED_SSH_CIDR="$(curl -s ifconfig.me)/32"
#      export ALLOWED_ADMIN_CIDR="$(curl -s ifconfig.me)/32"

# 3. Verify the VM is actually reachable before continuing
ssh azureuser@<public-ip> "echo connected"
#    If this hangs or refuses: check step 2's NSG rules actually allow
#    your current IP, and that the VM shows "VM running" in the console.

# 4. On the VM: get the code and install Docker
ssh azureuser@<public-ip>
#    ...now on the VM:
git clone <this-repository-url>
cd kdb-distributed-controlplane
bash deploy/azure/03_install_docker.sh
#    Read its final output carefully - it tells you exactly how to get a
#    shell with docker-group access before continuing (either reconnect
#    SSH, or run `newgrp docker`). Skipping this is the second most common
#    failure point: step 6 below fails with a permission error that has
#    nothing to do with anything else if you don't do this first.

# 5. Deploy
bash deploy/azure/04_deploy_stack.sh
#    If this refuses immediately with "cannot talk to the Docker daemon",
#    that's step 4's docker-group timing issue - see its own error message.
#    First run also copies .env.example to .env and stops so you can edit
#    it - see docs/getting-started.md step 2. Set KX_BEARER_TOKEN (a free
#    KX Developer Portal token - the KDB-X binary is pulled automatically
#    at container start, nothing to download/scp onto this box yourself)
#    and KDB_LICENSE_B64, plus LICENSE_KEY / DEPLOYMENT_ENV as this
#    script's own reminder says (this is a customer-facing path,
#    DEPLOYMENT_ENV is set to "customer" automatically, which makes a
#    valid LICENSE_KEY mandatory).
```

**Verify it's actually up** before telling anyone the URL:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://<public-ip>:8000/health   # expect 200
curl -s -o /dev/null -w "%{http_code}\n" http://<public-ip>/              # expect 200
```

Open `http://<public-ip>/` for the UI, enable the `bpipe-sim` and `crims-sim`
connectors, and watch the Metrics tab. The self-healing demo works the same as
elsewhere: kill a process on the Topology tab, watch the watchdog restore it,
check the Audit log. For a scripted walkthrough plus load numbers, run `demokit`
(see `demokit/README.md` and `DEMO.md`).

## Troubleshooting provisioning specifically

- **"VM size ... is not offered in location"** — `01_provision_vm.sh`
  already checks this before attempting creation and tells you how to find
  a region that does have it, or set `VM_SIZE=` to something the region
  does offer.
- **The image URN stops resolving** — Canonical has changed their image
  naming scheme before. Override with `IMAGE_URN=`; the script's own
  comment shows the `az vm image list` command to find the current one.
- **SSH hangs / connection refused** — almost always step 2's NSG rules
  (or `az vm create`'s own default SSH rule from step 1) not actually
  allowing your current IP. Re-run `./02_configure_networking.sh` with the
  current `ALLOWED_SSH_CIDR`.
- **`04_deploy_stack.sh` fails with a Docker permission error** — step 4's
  docker-group timing issue; see that step's note above.
- **Containers keep restarting after `04_deploy_stack.sh`** — almost always
  `KX_BEARER_TOKEN` missing/invalid, or `KDB_LICENSE_B64`/`KX_LICENSE_PATH`
  not set, in `.env`. See `docs/troubleshooting.md`.

## Deploying on a free-tier / brand-new Azure subscription

`01_provision_vm.sh` checks this automatically, with a real quota lookup
(not a guess): if no `VM_SIZE` is set and your subscription's regional
vCPU quota is too low for the default `Standard_D8s_v5`, it falls back on
its own to `Standard_B1s` (Azure's actual free-account VM size) with a
30GB disk, and skips accelerated networking + the proximity placement
group (B-series doesn't support the former, and neither is useful with one
box). You'll see a message explaining what it did and why.

Already know you're on a free/trial subscription? Skip straight there:
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

## Honest note on FPGA (why there isn't one)

Azure's FPGA family, the **NP-series** (AMD/Xilinx Alveo U250), is being wound
down, so building a new demo on it would be a mistake you'd have to walk back in
front of a client:

- New 1- and 3-year reserved-instance purchases **ended April 2, 2026**.
- Microsoft's own guidance is to **avoid deploying new NP-series VMs**.
- The bitstream **attestation service** you need to run a design on NP closed to
  new sign-ups in mid-2026.
- The whole NP family **retires May 31, 2027**, after which the VMs are
  deallocated.

Even setting the retirement aside, an NP VM wouldn't have accelerated the demo:
as on AWS, the FPGA does nothing until you build and attest a Vitis bitstream and
wire a feed handler to it — kdb+ never runs on the FPGA. So on Azure the honest
story is: use the CPU-side low-latency levers this module already sets
(accelerated networking + proximity placement + a compute-optimized size; bump
`VM_SIZE` to an `Standard_F*s_v2` for the highest clock), and if a client
genuinely needs FPGA-accelerated execution, that's an AWS F2 or an on-prem/colo
workstream — say so plainly rather than point at a retiring Azure family.

## Tearing down

```bash
export AZURE_RG=kdb-control-plane-demo-rg
./99_teardown.sh
```

Deletes the entire resource group (VM, disk, NIC, public IP, NSG, proximity
placement group all live in it — one clean delete). Run it as soon as you're
done — an idle VM keeps billing. Teardown runs in the background
(`--no-wait`); confirm it finished with `az group show --name
$AZURE_RG` (should eventually 404/error "not found").
