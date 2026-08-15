#!/usr/bin/env bash
# 01_provision_vm.sh - creates the GCP VM that will run the whole demo stack.
#
# IMPORTANT - read before running:
# GCP has no FPGA instance family (that is AWS F1/Xilinx territory - GCP has
# never offered FPGA-backed VMs). This script uses GCP's actual closest
# equivalents for low latency instead of pretending otherwise:
#   - a compute-optimized C3 machine type (highest per-core clock available)
#   - Tier_1 networking (doubles per-VM network bandwidth, requires gVNIC)
#   - a COMPACT placement policy (packs VMs onto adjacent physical hosts to
#     cut inter-VM network hop latency - matters once you split this single
#     VM into multiple VMs for real multi-node testing)
# For genuine FPGA acceleration you would need AWS F1 instances or an
# on-prem/colo FPGA appliance - that is a separate, non-GCP workstream.
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?set GCP_PROJECT_ID first}"
ZONE="${GCP_ZONE:-us-central1-a}"
REGION="${GCP_ZONE%-*}"
VM_NAME="${VM_NAME:-kdb-control-plane-demo}"
MACHINE_TYPE="${MACHINE_TYPE:-c3-standard-8}"   # 8 vCPU / 32GB, compute-optimized
DISK_SIZE_GB="${DISK_SIZE_GB:-100}"
IMAGE_FAMILY="${IMAGE_FAMILY:-ubuntu-2204-lts}"
IMAGE_PROJECT="${IMAGE_PROJECT:-ubuntu-os-cloud}"

echo "== setting active project =="
gcloud config set project "$PROJECT_ID"

echo "== enabling required APIs =="
gcloud services enable compute.googleapis.com

# Machine-type availability per zone is the #1 reported failure point of
# this script - compute-optimized C3 has materially narrower zone coverage
# than general-purpose families, and picking a zone that doesn't have it
# otherwise surfaces as a buried "not available" error from `instances
# create`. Check first, with a clear fix.
echo "== checking '$MACHINE_TYPE' is offered in $ZONE =="
OFFERED="$(gcloud compute machine-types list \
  --filter="name=$MACHINE_TYPE AND zone:$ZONE" \
  --format='value(name)' 2>/dev/null || true)"
if [ -z "$OFFERED" ]; then
  cat >&2 <<EOF

ERROR: machine type '$MACHINE_TYPE' is not offered in zone '$ZONE'.

Fix: either pick a zone that has it -
  gcloud compute machine-types list --filter="name=$MACHINE_TYPE" \\
    --format='value(zone)'
or pick a machine type this zone does have and re-run with it set:
  MACHINE_TYPE=n2-standard-8 ./01_provision_vm.sh

EOF
  exit 1
fi

echo "== creating a COMPACT placement policy (adjacent-host packing) =="
gcloud compute resource-policies create group-placement \
  "${VM_NAME}-placement" \
  --region "$REGION" \
  --collocation COLLOCATED \
  || echo "placement policy already exists, continuing"

echo "== creating the VM (C3, Tier_1 networking via gVNIC, compact-placed) =="
gcloud compute instances create "$VM_NAME" \
  --zone "$ZONE" \
  --machine-type "$MACHINE_TYPE" \
  --image-family="$IMAGE_FAMILY" \
  --image-project="$IMAGE_PROJECT" \
  --boot-disk-size "${DISK_SIZE_GB}GB" \
  --boot-disk-type=pd-ssd \
  --network-interface=nic-type=GVNIC \
  --network-performance-configs=total-egress-bandwidth-tier=TIER_1 \
  --resource-policies="${VM_NAME}-placement" \
  --tags=kdb-control-plane-demo \
  --metadata=enable-oslogin=true

echo "== reserving a static external IP =="
gcloud compute addresses create "${VM_NAME}-ip" --region "$REGION" || echo "IP already reserved, continuing"
gcloud compute instances add-access-config "$VM_NAME" \
  --zone "$ZONE" \
  --address "$(gcloud compute addresses describe "${VM_NAME}-ip" --region "$REGION" --format='get(address)')" \
  || echo "access config may already be set, check manually if this failed"

echo "== done - VM external IP: =="
gcloud compute instances describe "$VM_NAME" --zone "$ZONE" \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
