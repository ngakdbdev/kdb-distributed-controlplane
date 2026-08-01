#!/usr/bin/env bash
# 02_configure_networking.sh - opens only what the demo needs on the NSG that
# `az vm create` made for the VM: HTTP (web UI) and the control-API port. SSH
# (22) is already opened by az vm create. Run AFTER 01_provision_vm.sh.
set -euo pipefail

RG="${AZURE_RG:-kdb-control-plane-demo-rg}"
VM_NAME="${VM_NAME:-kdb-control-plane-demo}"
NSG="${NSG_NAME:-${VM_NAME}NSG}"   # az vm create names it <vmname>NSG

echo "== using NSG '$NSG' in '$RG' =="

echo "== restrict SSH to your own IP (optional but recommended) =="
if [ -n "${ALLOWED_SSH_CIDR:-}" ]; then
  az network nsg rule create --resource-group "$RG" --nsg-name "$NSG" \
    --name allow-ssh --priority 1000 --access Allow --protocol Tcp --direction Inbound \
    --destination-port-ranges 22 --source-address-prefixes "$ALLOWED_SSH_CIDR" \
    --output none 2>/dev/null || \
  az network nsg rule update --resource-group "$RG" --nsg-name "$NSG" \
    --name allow-ssh --source-address-prefixes "$ALLOWED_SSH_CIDR" --output none
else
  echo "   ALLOWED_SSH_CIDR not set - leaving az vm create's default SSH rule as-is"
fi

echo "== allow HTTP for the web UI =="
az network nsg rule create --resource-group "$RG" --nsg-name "$NSG" \
  --name allow-http --priority 1001 --access Allow --protocol Tcp --direction Inbound \
  --destination-port-ranges 80 --source-address-prefixes '*' \
  --output none 2>/dev/null || echo "   http rule already exists, continuing"

echo "== allow direct control-api access for debugging (tighten/remove for real client demos) =="
az network nsg rule create --resource-group "$RG" --nsg-name "$NSG" \
  --name allow-control-api --priority 1002 --access Allow --protocol Tcp --direction Inbound \
  --destination-port-ranges 8000 --source-address-prefixes "${ALLOWED_ADMIN_CIDR:-*}" \
  --output none 2>/dev/null || echo "   control-api rule already exists, continuing"

echo
echo "== done - NSG rules in place. Note: kdb+ IPC ports (5010-5050) are"
echo "   deliberately NOT exposed - they stay on the Docker bridge network and"
echo "   are only reachable from other containers on the VM."
