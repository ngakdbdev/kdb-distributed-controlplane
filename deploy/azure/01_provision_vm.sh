#!/usr/bin/env bash
# 01_provision_vm.sh - creates the Azure resource group + VM that runs the demo.
#
# IMPORTANT - read before running:
# This deliberately does NOT use Azure's FPGA family. The NP-series (Alveo U250
# FPGA) is being retired: new reserved-instance purchases ended April 2026,
# Microsoft advises against deploying new NP VMs, the bitstream attestation
# service has closed to new sign-ups, and the family retires May 31, 2027. So
# Azure FPGA is a dead end for a new build - see deploy/azure/README.md.
#
# Instead this uses Azure's real low-latency levers:
#   - accelerated networking (SR-IOV, bypasses the host vSwitch)
#   - a proximity placement group (co-locates VMs in one datacenter to cut
#     inter-VM latency - matters once you split this into multiple nodes)
#   - a compute-optimized VM size (override to an Fsv2 for highest clock)
set -euo pipefail

LOCATION="${AZURE_LOCATION:-eastus}"
RG="${AZURE_RG:-kdb-control-plane-demo-rg}"
VM_NAME="${VM_NAME:-kdb-control-plane-demo}"
VM_SIZE="${VM_SIZE:-Standard_D8s_v5}"     # 8 vCPU / 32 GiB, accelerated-net capable
DISK_SIZE_GB="${DISK_SIZE_GB:-100}"
ADMIN_USER="${ADMIN_USER:-azureuser}"
PPG="${VM_NAME}-ppg"
# Canonical's own image-naming scheme has shifted before (the older
# "UbuntuServer:18.04-LTS" style moved to this "0001-com-ubuntu-server-*"
# scheme) - override if this exact URN ever stops resolving for your
# subscription/region. Find the current one with:
#   az vm image list --publisher Canonical --sku 22_04-lts-gen2 --all --output table
IMAGE_URN="${IMAGE_URN:-Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest}"

echo "== creating resource group '$RG' in $LOCATION =="
az group create --name "$RG" --location "$LOCATION" --output none

# VM size availability per region is the #1 reported failure point of this
# script (it looks like an unrelated error deep in `az vm create`'s output
# otherwise) - not every VM size is offered in every region, and the
# accelerated-networking-capable Dsv5/Fsv2 families roll out region by
# region on their own schedule. Check before attempting creation, with a
# clear fix instead of a buried Azure error message.
echo "== checking '$VM_SIZE' is offered in $LOCATION =="
OFFERED="$(az vm list-sizes --location "$LOCATION" \
  --query "[?name=='$VM_SIZE'].name" --output tsv 2>/dev/null || true)"
if [ -z "$OFFERED" ]; then
  cat >&2 <<EOF

ERROR: VM size '$VM_SIZE' is not offered in location '$LOCATION'.

Fix: either pick a location that has it -
  az vm list-skus --location $LOCATION --size $VM_SIZE --output table
  # (empty output = not offered here; try another AZURE_LOCATION)
or pick a size this location does have and re-run with it set:
  VM_SIZE=Standard_D8s_v4 ./01_provision_vm.sh

EOF
  exit 1
fi

echo "== creating a proximity placement group =="
az ppg create --name "$PPG" --resource-group "$RG" --location "$LOCATION" \
  --output none 2>/dev/null || echo "   ppg already exists, continuing"

echo "== creating the VM ($VM_SIZE, accelerated networking, proximity-placed) =="
echo "   image: $IMAGE_URN"
az vm create \
  --resource-group "$RG" \
  --name "$VM_NAME" \
  --image "$IMAGE_URN" \
  --size "$VM_SIZE" \
  --admin-username "$ADMIN_USER" \
  --generate-ssh-keys \
  --accelerated-networking true \
  --ppg "$PPG" \
  --public-ip-sku Standard \
  --os-disk-size-gb "$DISK_SIZE_GB" \
  --tags app=kdb-control-plane-demo \
  --output none

PUBLIC_IP="$(az vm show -d --resource-group "$RG" --name "$VM_NAME" \
  --query publicIps --output tsv)"

echo
echo "== done =="
echo "Resource group: $RG"
echo "Public IP:      $PUBLIC_IP"
echo "SSH:            ssh ${ADMIN_USER}@${PUBLIC_IP}"
echo
echo "Next: ./02_configure_networking.sh to open HTTP + the control-api port."
