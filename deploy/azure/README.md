# Deploying to Azure

Mirrors the GCP module (`deploy/gcp/`): provision → networking → docker →
deploy → teardown. Uses Azure's real low-latency levers (accelerated
networking, proximity placement group, compute-optimized VM size). There is
**no FPGA option** here on purpose — see the note below.

## Before you start

1. Install the Azure CLI and run `az login`.
2. Pick a region with `export AZURE_LOCATION=eastus` (or nearer your audience).
3. Download **KDB-X Community Edition** (free, commercial use allowed) from the
   KX Developer Center — the Linux `q` binary and your `k4.lic`. This repo can't
   bundle them; KX's terms require you to obtain them directly.

## Steps

```bash
export AZURE_LOCATION=eastus
export VM_NAME=kdb-control-plane-demo

# 1. Provision RG + VM (accelerated networking, proximity placement group)
./01_provision_vm.sh
#    -> prints the public IP and the ssh command

# 2. Open HTTP + the control-api port on the VM's NSG
./02_configure_networking.sh

# 3. SSH in and install Docker
ssh azureuser@<public-ip>
#    ...on the VM:
curl -O https://raw.githubusercontent.com/<your-repo>/main/deploy/azure/03_install_docker.sh
bash 03_install_docker.sh
newgrp docker

# 4. Get the project onto the VM (git clone, or scp the zip), then:
cd kdb-distributed-controlplane
mkdir -p data-plane/docker/kdbx
#    copy your `q` binary and `k4.lic` into data-plane/docker/kdbx/
cp .env.example .env
#    edit .env: set ADMIN_PASSWORD_HASH, JWT_SECRET, WATCHDOG_SHARED_SECRET

# 5. Deploy
bash deploy/azure/04_deploy_stack.sh
```

Open `http://<public-ip>/` for the UI, enable the `bpipe-sim` and `crims-sim`
connectors, and watch the Metrics tab. The self-healing demo works the same as
elsewhere: kill a process on the Topology tab, watch the watchdog restore it,
check the Audit log. For a scripted walkthrough plus load numbers, run `demokit`
(see `demokit/README.md` and `DEMO.md`).

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

Deletes the entire resource group. Run it as soon as you're done — an idle VM
keeps billing.
