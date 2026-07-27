#!/usr/bin/env bash
# 02_configure_networking.sh - opens only what the demo needs: SSH, HTTP
# (web UI), and the control API port for direct debugging.
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?set GCP_PROJECT_ID first}"
gcloud config set project "$PROJECT_ID"

echo "== allow SSH (restrict source-ranges to your own IP in production) =="
gcloud compute firewall-rules create kdb-allow-ssh \
  --allow tcp:22 \
  --target-tags kdb-control-plane-demo \
  --source-ranges "${ALLOWED_SSH_CIDR:-0.0.0.0/0}" \
  || echo "rule already exists, continuing"

echo "== allow HTTP for the web UI =="
gcloud compute firewall-rules create kdb-allow-http \
  --allow tcp:80 \
  --target-tags kdb-control-plane-demo \
  --source-ranges "0.0.0.0/0" \
  || echo "rule already exists, continuing"

echo "== allow direct control-api access for debugging (tighten/remove for real client demos) =="
gcloud compute firewall-rules create kdb-allow-control-api \
  --allow tcp:8000 \
  --target-tags kdb-control-plane-demo \
  --source-ranges "${ALLOWED_ADMIN_CIDR:-0.0.0.0/0}" \
  || echo "rule already exists, continuing"

echo "== done - firewall rules in place. Note: kdb+ IPC ports (5010-5050) are"
echo "   deliberately NOT exposed externally - they stay on the Docker bridge"
echo "   network and are only reachable from other containers on the VM."
