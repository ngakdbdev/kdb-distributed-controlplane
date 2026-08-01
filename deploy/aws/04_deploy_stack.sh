#!/usr/bin/env bash
# 04_deploy_stack.sh - run this ON the instance, from inside the project
# directory, after 03_install_docker.sh and after placing your KDB-X
# binary/license at data-plane/docker/kdbx/ (see deploy/aws/README.md).
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

# EC2 IMDSv2 (token-first) to discover our own public IP
TOKEN="$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 300" || true)"
PUBLIC_IP="$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/public-ipv4 || true)"
INSTANCE_TYPE="$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/instance-type || true)"

echo
echo "== deployed =="
echo "Web UI:      http://${PUBLIC_IP:-<public-ip>}/"
echo "Control API: http://${PUBLIC_IP:-<public-ip>}:8000/docs"
echo
echo "Log in as the seeded platform or demo-tenant admin (see .env password hashes),"
echo "then enable the bpipe-sim and crims-sim connectors from the Connectors tab."

case "$INSTANCE_TYPE" in
  f2.*)
    echo
    echo "NOTE: this is an FPGA instance ($INSTANCE_TYPE), but the FPGA is idle."
    echo "The demo runs entirely on CPU. Loading an AFI/bitstream and wiring a"
    echo "feed handler to it is a separate workstream - see deploy/aws/README.md."
    ;;
esac
