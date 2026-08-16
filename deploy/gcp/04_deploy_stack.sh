#!/usr/bin/env bash
# 04_deploy_stack.sh - run this ON the VM, from inside the project directory,
# after 03_install_docker.sh and after placing your KDB-X binary/license at
# data-plane/docker/kdbx/ (see README.md in this deploy/gcp folder).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/free_tier.sh
source "$SCRIPT_DIR/../lib/free_tier.sh"
# shellcheck source=../lib/kx_license.sh
source "$SCRIPT_DIR/../lib/kx_license.sh"
detect_lean_mode

# 03_install_docker.sh grants docker-group membership, but that only takes
# effect in a FRESH shell session - if you ran 03 then 04 back-to-back in
# the same SSH session, this fails with a permission error that has
# nothing to do with anything below. Check for it explicitly instead of
# letting it surface as a confusing docker error three steps in.
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: cannot talk to the Docker daemon as $(whoami)." >&2
  echo "If you just ran 03_install_docker.sh in THIS SAME shell session," >&2
  echo "your docker-group membership hasn't taken effect yet - expected," >&2
  echo "not a bug. Fix: run 'newgrp docker' (or disconnect/reconnect SSH)," >&2
  echo "then re-run this script." >&2
  exit 1
fi

if [ ! -f .env ]; then
  echo "No .env found - copying .env.example. EDIT IT before continuing:"
  cp .env.example .env
  # This is a customer-facing deployment path (not your own laptop) - a
  # product licence key is mandatory here (see control-api/app/licensing.py).
  # DEPLOYMENT_ENV=customer turns that enforcement on by default; set
  # DEPLOYMENT_ENV=local instead only if you deliberately want this box
  # unenforced (e.g. a throwaway sales-team test box, not a real customer).
  if grep -q '^DEPLOYMENT_ENV=' .env; then
    sed -i.bak 's/^DEPLOYMENT_ENV=.*/DEPLOYMENT_ENV=customer/' .env && rm -f .env.bak
  else
    echo "DEPLOYMENT_ENV=customer" >> .env
  fi
  echo "  -> set ADMIN_PASSWORD_HASH, JWT_SECRET, WATCHDOG_SHARED_SECRET, LICENSE_KEY"
  exit 1
fi

apply_lean_mode

check_kx_binary_and_license || exit 1

echo "== building all images (this is the slow step the first time) =="
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

EXTERNAL_IP="$(curl -s -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip || true)"

echo
echo "== deployed =="
echo "Web UI:      http://${EXTERNAL_IP:-<vm-external-ip>}/"
echo "Control API: http://${EXTERNAL_IP:-<vm-external-ip>}:8000/docs"
echo
echo "Default login is admin / changeme unless you set ADMIN_PASSWORD_HASH in .env"
echo "Enable the bpipe-sim and crims-sim connectors from the UI's Connectors tab to start traffic."
