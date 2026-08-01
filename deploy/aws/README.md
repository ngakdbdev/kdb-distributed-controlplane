# Deploying to AWS

Mirrors the GCP module (`deploy/gcp/`): provision → networking → docker →
deploy → teardown. By default it uses a compute-optimized, high-clock EC2
instance with AWS's real low-latency levers, not an FPGA instance. FPGA is an
explicit, off-by-default opt-in — read the FPGA section before you reach for it.

## Before you start

1. Install the AWS CLI v2 and run `aws configure` (or use SSO). The identity you
   use needs EC2 create/terminate, security-group, placement-group, and EIP
   permissions.
2. Pick a region with `export AWS_REGION=us-east-1` (or nearer your audience).
3. Download **KDB-X Community Edition** (free, commercial use allowed) from the
   KX Developer Center — the Linux `q` binary and your `k4.lic`. This repo can't
   bundle them; KX's terms require you to obtain them directly.

## Steps

```bash
export AWS_REGION=us-east-1
export VM_NAME=kdb-control-plane-demo

# 1. Network first (creates the security group the instance attaches to)
./02_configure_networking.sh

# 2. Provision the instance (C7i, cluster placement group, ENA)
./01_provision_vm.sh
#    -> prints the public IP and the exact ssh command

# 3. SSH in and install Docker
ssh -i ${VM_NAME}-key.pem ubuntu@<public-ip>
#    ...on the instance:
curl -O https://raw.githubusercontent.com/<your-repo>/main/deploy/aws/03_install_docker.sh
bash 03_install_docker.sh
newgrp docker

# 4. Get the project onto the instance (git clone, or scp the zip), then:
cd kdb-distributed-controlplane
mkdir -p data-plane/docker/kdbx
#    copy your `q` binary and `k4.lic` into data-plane/docker/kdbx/
cp .env.example .env
#    edit .env: set ADMIN_PASSWORD_HASH, JWT_SECRET, WATCHDOG_SHARED_SECRET

# 5. Deploy
bash deploy/aws/04_deploy_stack.sh
```

Open `http://<public-ip>/` for the UI, enable the `bpipe-sim` and `crims-sim`
connectors, and watch the Metrics tab. The self-healing demo works the same as
on GCP: kill a process on the Topology tab and watch the watchdog restore it,
then check the Audit log. For a fully scripted walkthrough plus load numbers,
run `demokit` (see `demokit/README.md` and `DEMO.md`).

## Honest note on FPGA (the opt-in)

AWS **does** have FPGA instances — F1, and the current-generation **F2** (up to
8 AMD Virtex UltraScale+ FPGAs, first F2 sizes GA Dec 2024, with `f2.6xlarge`
being the single-FPGA entry size). Set `ENABLE_FPGA=1` before `01_provision_vm.sh`
to launch one (`f2.6xlarge` by default; override with `FPGA_INSTANCE_TYPE`).

What that flag does **not** do — and what to say plainly to a client:

- It only provisions the FPGA-*capable* box. The FPGA is inert until you design,
  compile (hours in Vitis/HLS), and load a custom **Amazon FPGA Image (AFI)**
  built with the AWS FPGA Development Kit, then wire your feed handler to offload
  to it. kdb+ never runs on the FPGA, and stock q gains nothing from an F2 over
  a C7i — the demo runs entirely on CPU either way.
- Where FPGAs actually earn their keep in tick/finance is ultra-low-latency
  market-data decoding and tick-to-trade, and that is overwhelmingly done on-prem
  with kernel-bypass NICs, not on a cloud FPGA VM. Treat cloud FPGA as "we can
  host your accelerator design," not "kdb+ is now FPGA-accelerated."
- Operational gotchas: F2 needs a **service-quota increase** (the default F
  instance limit is often 0), is **Linux-only**, and is only in **some regions**
  (N. Virginia, Oregon, London, Frankfurt, Tokyo, Seoul, Sydney, Canada Central
  at the time of writing — check the current list). And it's expensive: tear it
  down the moment you're done.

The default (C7i + cluster placement + ENA) is the right choice for showing the
control plane. Only reach for `ENABLE_FPGA=1` if a client genuinely has an FPGA
accelerator design in play and wants it hosted in AWS.

## Tearing down

```bash
export AWS_REGION=us-east-1
export VM_NAME=kdb-control-plane-demo
./99_teardown.sh
```

Run it as soon as you're done — an idle instance keeps billing, and an idle F2
keeps billing a lot.
