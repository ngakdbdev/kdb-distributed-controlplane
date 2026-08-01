#!/usr/bin/env bash
# 04_deploy_stack.sh - run this ON the VM, from inside the project directory,
# after 03_install_docker.sh and after placing your KDB-X binary/license at
# data-plane/docker/kdbx/ (see deploy/azure/README.md).
set -euo pipefail

if [ ! -f .env ]; then
  echo "No .env found - copying .env.example. EDIT IT before continuing:"
  cp .env.example .env
  echo "  -> set ADMIN_PASSWORD_HASH, JWT_SECRET, WATCHDOG_SHARED_SECRET"
  exit 1
fi

if [ ! -f data-plane/docker/kdbx/q ] || [ ! -f data-plane/docker/kdbx/k4.lic ]; then
  echo "Missing KDB-X binary/license at data-plane/docker/kdbx/"
  echo "Download KDB-X Community Edition from the KX Developer Center and place"
  echo "the linux 'q' binary and 'k4.lic' license file there, then re-run."
  exit 1
fi

echo "== building all images (slow the first time) =="
docker compose build

echo "== bringing the stack up =="
docker compose up -d

echo "== waiting for the control API to come up =="
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/health > /dev/null; then
    echo "control API is up"
    break
  fi
  sleep 2
done

echo "== current container status =="
docker compose ps

# Azure Instance Metadata Service to discover our own public IP
PUBLIC_IP="$(curl -s -H "Metadata:true" \
  "http://169.254.169.254/metadata/instance/network/interface/0/ipv4/ipAddress/0/publicIpAddress?api-version=2021-02-01&format=text" || true)"

echo
echo "== deployed =="
echo "Web UI:      http://${PUBLIC_IP:-<public-ip>}/"
echo "Control API: http://${PUBLIC_IP:-<public-ip>}:8000/docs"
echo
echo "Log in as the seeded platform or demo-tenant admin (see .env password hashes),"
echo "then enable the bpipe-sim and crims-sim connectors from the Connectors tab."
