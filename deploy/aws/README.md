# Deploying to AWS

Mirrors the GCP and Azure modules: provision → networking → docker →
deploy → teardown. By default it uses a compute-optimized, high-clock EC2
instance with AWS's real low-latency levers, not an FPGA instance. FPGA is an
explicit, off-by-default opt-in — read the FPGA section before you reach for it.

This is the **single-VM, throwaway, one-prospect-demo path** — not
production-grade (no HA, blast radius is the whole box). If you're taking
this to a real customer beyond a one-off demo, read
[docs/getting-started.md](../../docs/getting-started.md)'s hardening
pointers and [docs/deployment-process.md](../../docs/deployment-process.md)
first — this README covers *provisioning*, not everything you should do
before something is customer-facing.

## Before you start

1. Install the AWS CLI v2 and run `aws configure` (or use SSO). Confirm it
   works: `aws sts get-caller-identity` should print your account/identity,
   not an error. The identity you use needs EC2 create/terminate,
   security-group, placement-group, and EIP permissions, plus
   `ssm:GetParameters` and `ec2:DescribeImages` (used to resolve the AMI —
   see the AMI troubleshooting note below).
2. Pick a region: `export AWS_REGION=us-east-1` (or nearer your audience).
   Not every instance type is available in every region — if step 2 below
   fails on this, the error tells you exactly how to check and what to
   override.
3. Download **KDB-X Community Edition** (free, commercial use allowed) from
   the KX Developer Center — the Linux `q` binary and your `kc.lic`. This
   repo can't bundle them; KX's terms require you to obtain them directly.

## Steps

```bash
export AWS_REGION=us-east-1
export VM_NAME=kdb-control-plane-demo

# 1. Network first (creates the security group the instance attaches to)
./02_configure_networking.sh
#    By default this opens SSH (22) and the control-api debug port (8000)
#    to the whole internet (0.0.0.0/0) - fine for a five-minute throwaway
#    demo, NOT fine for anything left running. Set ALLOWED_SSH_CIDR and
#    ALLOWED_ADMIN_CIDR to your own IP (e.g. "$(curl -s ifconfig.me)/32")
#    before running this for anything you'll leave up:
#      export ALLOWED_SSH_CIDR="$(curl -s ifconfig.me)/32"
#      export ALLOWED_ADMIN_CIDR="$(curl -s ifconfig.me)/32"

# 2. Provision the instance (C7i, cluster placement group, ENA)
./01_provision_vm.sh
#    -> prints the public IP and the exact ssh command
#
#    If this fails with an AMI or instance-type error, the script's own
#    output tells you exactly how to check availability and gives you an
#    override (AMI_ID=... or INSTANCE_TYPE=...) - re-run with it set. This
#    is the single most common failure point of this whole path, almost
#    always caused by the chosen region not (yet) offering the specific
#    AMI/instance-type combination, not a real problem with your account.

# 3. Verify the instance is actually reachable before continuing
ssh -i ${VM_NAME}-key.pem ubuntu@<public-ip> "echo connected"
#    If this hangs or refuses: check 02's security group actually allows
#    your current IP (ALLOWED_SSH_CIDR), and that the instance shows
#    "running" in the EC2 console, not still "pending".

# 4. On the instance: get the code and install Docker
ssh -i ${VM_NAME}-key.pem ubuntu@<public-ip>
#    ...now on the instance:
git clone <this-repository-url>
cd kdb-distributed-controlplane
bash deploy/aws/03_install_docker.sh
#    Read its final output carefully - it tells you exactly how to get a
#    shell with docker-group access before continuing (either reconnect
#    SSH, or run `newgrp docker`). Skipping this is the second most common
#    failure point: step 5 below fails with a permission error that has
#    nothing to do with anything else if you don't do this first.

# 5. Place your KDB-X binary + licence
mkdir -p data-plane/docker/kdbx
#    copy your `q` binary and `kc.lic` into data-plane/docker/kdbx/ now
#    (scp them from your laptop, or download directly on the instance)

# 6. Deploy
bash deploy/aws/04_deploy_stack.sh
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
curl -s -o /dev/null -w "%{http_code}\n" http://<public-ip>:8000/health   # expect 200
curl -s -o /dev/null -w "%{http_code}\n" http://<public-ip>/              # expect 200
```

Open `http://<public-ip>/` for the UI, enable the `bpipe-sim` and `crims-sim`
connectors, and watch the Metrics tab. The self-healing demo works the same as
on GCP/Azure: kill a process on the Topology tab and watch the watchdog restore it,
then check the Audit log. For a fully scripted walkthrough plus load numbers,
run `demokit` (see `demokit/README.md` and `DEMO.md`).

## Troubleshooting provisioning specifically

- **"could not resolve an Ubuntu 22.04 AMI"** — `01_provision_vm.sh` already
  tries a fallback lookup and tells you exactly what's wrong (usually an
  IAM permission gap or the region not having it yet) and how to override
  with `AMI_ID=ami-xxxx`. Don't hand-edit the script; set the env var.
- **"instance type ... is not offered in region"** — same script, same
  pattern: it tells you how to check which regions do have it, or set
  `INSTANCE_TYPE=` to something the region does offer.
- **SSH hangs / connection refused** — almost always the security group
  (step 1) not actually allowing your current IP, especially if you set
  `ALLOWED_SSH_CIDR` to a value that's since changed (e.g. you're on a
  different network now than when you ran the script). Re-run
  `./02_configure_networking.sh` with the current `ALLOWED_SSH_CIDR`.
- **`04_deploy_stack.sh` fails with a Docker permission error** — step 4's
  docker-group timing issue; see that step's note above.
- **Containers keep restarting after `04_deploy_stack.sh`** — almost always
  the KDB-X binary/licence (step 5) not actually being at
  `data-plane/docker/kdbx/`, or being the wrong architecture. See
  `docs/troubleshooting.md`.

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
keeps billing a lot. `99_teardown.sh` deletes the instance, Elastic IP,
placement group, and security group; it deliberately leaves the SSH key pair
in place (delete it yourself if you're fully done: `aws ec2 delete-key-pair
--key-name ${VM_NAME}-key`, and remove the local `.pem` file).
