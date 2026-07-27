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

echo "== setting active project =="
gcloud config set project "$PROJECT_ID"

echo "== enabling required APIs =="
gcloud services enable compute.googleapis.com

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
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
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
