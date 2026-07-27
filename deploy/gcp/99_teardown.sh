#!/usr/bin/env bash
# 99_teardown.sh - deletes everything this deployment created, so the demo
# doesn't quietly keep burning GCP trial credit after you're done with it.
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?set GCP_PROJECT_ID first}"
ZONE="${GCP_ZONE:-us-central1-a}"
REGION="${GCP_ZONE%-*}"
VM_NAME="${VM_NAME:-kdb-control-plane-demo}"

gcloud config set project "$PROJECT_ID"

echo "== deleting VM =="
gcloud compute instances delete "$VM_NAME" --zone "$ZONE" --quiet || true

echo "== deleting static IP =="
gcloud compute addresses delete "${VM_NAME}-ip" --region "$REGION" --quiet || true

echo "== deleting placement policy =="
gcloud compute resource-policies delete "${VM_NAME}-placement" --region "$REGION" --quiet || true

echo "== deleting firewall rules =="
gcloud compute firewall-rules delete kdb-allow-ssh kdb-allow-http kdb-allow-control-api --quiet || true

echo "== done - check the GCP console billing page to confirm nothing is still running =="
