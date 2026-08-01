#!/usr/bin/env bash
# 99_teardown.sh - deletes the whole resource group this deployment created,
# so nothing keeps billing after you're done. Everything (VM, disk, NIC, public
# IP, NSG, proximity placement group) lives in that one RG, so this is a clean
# single delete.
set -euo pipefail

RG="${AZURE_RG:-kdb-control-plane-demo-rg}"

echo "== deleting resource group '$RG' (this removes ALL resources in it) =="
az group delete --name "$RG" --yes --no-wait

echo "== delete requested (running in the background) =="
echo "   confirm with: az group show --name $RG   (should eventually 404)"
