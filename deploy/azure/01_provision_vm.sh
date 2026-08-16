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
ADMIN_USER="${ADMIN_USER:-azureuser}"
PPG="${VM_NAME}-ppg"
# Canonical's own image-naming scheme has shifted before (the older
# "UbuntuServer:18.04-LTS" style moved to this "0001-com-ubuntu-server-*"
# scheme) - override if this exact URN ever stops resolving for your
# subscription/region. Find the current one with:
#   az vm image list --publisher Canonical --sku 22_04-lts-gen2 --all --output table
IMAGE_URN="${IMAGE_URN:-Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest}"

# FREE_TIER=1 - explicit "I'm on/want a free-eligible subscription" signal.
# Standard_B1s (1 vCPU, 1GiB) is Azure's actual free-account VM size - not
# a guess. B-series burstable VMs don't support accelerated networking and
# gain nothing from a proximity placement group with only one box, so both
# are skipped too.
FREE_TIER="${FREE_TIER:-0}"
FREE_TIER_VM_SIZE="${FREE_TIER_VM_SIZE:-Standard_B1s}"
FREE_TIER_DISK_SIZE_GB="${FREE_TIER_DISK_SIZE_GB:-30}"

# Captured before the default is applied, so the auto-fallback below only
# ever kicks in when the CALLER didn't ask for a specific size - an
# explicit VM_SIZE/DISK_SIZE_GB is always respected as-is, never
# second-guessed. (Must capture DISK_SIZE_GB here too, not inline in the
# FREE_TIER branch below - by then the "${DISK_SIZE_GB:-100}" default a
# few lines down has already made it non-empty, so a later
# "${DISK_SIZE_GB:-$FREE_TIER_DISK_SIZE_GB}" would never actually fall
# back to the free-tier size.)
_VM_SIZE_EXPLICIT="${VM_SIZE:-}"
_DISK_SIZE_GB_EXPLICIT="${DISK_SIZE_GB:-}"
VM_SIZE="${VM_SIZE:-Standard_D8s_v5}"     # 8 vCPU / 32 GiB, accelerated-net capable
DISK_SIZE_GB="${DISK_SIZE_GB:-100}"

echo "== creating resource group '$RG' in $LOCATION =="
az group create --name "$RG" --location "$LOCATION" --output none

if [ "$FREE_TIER" = "1" ]; then
  VM_SIZE="$FREE_TIER_VM_SIZE"
  DISK_SIZE_GB="${_DISK_SIZE_GB_EXPLICIT:-$FREE_TIER_DISK_SIZE_GB}"
  echo "== FREE_TIER=1: provisioning $VM_SIZE / ${DISK_SIZE_GB}GB (Azure's actual"
  echo "   free-account VM size) - skipping accelerated networking + the"
  echo "   proximity placement group =="
elif [ -z "$_VM_SIZE_EXPLICIT" ]; then
  # No explicit size and not an explicit FREE_TIER ask - best-effort check
  # of whether THIS subscription's regional vCPU quota can even fit the
  # compute-optimized default before attempting `az vm create` at all. Real
  # Azure usage/quota data, not a guess: "Total Regional vCPUs" is always
  # present per-location and is exactly what a Standard_D8s_v5 (8 vCPU)
  # draws against. New/free-trial subscriptions commonly start this low
  # enough that it won't fit. Soft-fails (leaves VM_SIZE untouched) on ANY
  # error here - a permissions gap or a transient API issue must never
  # block a deploy that might otherwise have worked; `az vm create` remains
  # the final word.
  echo "== checking this subscription's regional vCPU quota can fit $VM_SIZE =="
  NEEDED_VCPUS="$(az vm list-sizes --location "$LOCATION" \
    --query "[?name=='$VM_SIZE'].numberOfCores | [0]" --output tsv 2>/dev/null || true)"
  USAGE_TSV="$(az vm list-usage --location "$LOCATION" \
    --query "[?localName=='Total Regional vCPUs'].[currentValue,limit] | [0]" \
    --output tsv 2>/dev/null || true)"
  CURRENT_VCPUS="$(echo "$USAGE_TSV" | awk '{print $1}')"
  LIMIT_VCPUS="$(echo "$USAGE_TSV" | awk '{print $2}')"
  if [ -n "$NEEDED_VCPUS" ] && [ -n "$CURRENT_VCPUS" ] && [ -n "$LIMIT_VCPUS" ] \
     && awk -v c="$CURRENT_VCPUS" -v l="$LIMIT_VCPUS" -v n="$NEEDED_VCPUS" \
       'BEGIN{exit !((l-c)<n)}' 2>/dev/null; then
    echo "   available regional vCPU headroom ($((LIMIT_VCPUS - CURRENT_VCPUS))) is below"
    echo "   what $VM_SIZE needs (${NEEDED_VCPUS}) - looks like a free-trial or"
    echo "   brand-new subscription. Falling back to $FREE_TIER_VM_SIZE."
    echo "   Force the original size anyway with:"
    echo "     VM_SIZE=$VM_SIZE ./01_provision_vm.sh"
    VM_SIZE="$FREE_TIER_VM_SIZE"
    DISK_SIZE_GB="${_DISK_SIZE_GB_EXPLICIT:-$FREE_TIER_DISK_SIZE_GB}"
    FREE_TIER=1
  else
    echo "   quota check inconclusive or sufficient - continuing with $VM_SIZE"
  fi
fi

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

EXTRA_VM_ARGS=()
TAGS=(app=kdb-control-plane-demo)
if [ "$FREE_TIER" = "1" ]; then
  echo "== FREE_TIER=1: skipping the proximity placement group =="
  TAGS+=(tier=free)
else
  echo "== creating a proximity placement group =="
  az ppg create --name "$PPG" --resource-group "$RG" --location "$LOCATION" \
    --output none 2>/dev/null || echo "   ppg already exists, continuing"
  EXTRA_VM_ARGS=(--accelerated-networking true --ppg "$PPG")
fi

echo "== creating the VM ($VM_SIZE) =="
echo "   image: $IMAGE_URN"
az vm create \
  --resource-group "$RG" \
  --name "$VM_NAME" \
  --image "$IMAGE_URN" \
  --size "$VM_SIZE" \
  --admin-username "$ADMIN_USER" \
  --generate-ssh-keys \
  ${EXTRA_VM_ARGS[@]+"${EXTRA_VM_ARGS[@]}"} \
  --public-ip-sku Standard \
  --os-disk-size-gb "$DISK_SIZE_GB" \
  --tags "${TAGS[@]}" \
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
