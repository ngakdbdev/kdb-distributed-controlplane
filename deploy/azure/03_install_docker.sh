#!/usr/bin/env bash
# 03_install_docker.sh - run this ON the VM (SSH in first):
#   ssh azureuser@<public-ip>
# then: bash 03_install_docker.sh
set -euo pipefail

echo "== installing Docker Engine + Compose plugin =="
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "== allowing the current user to run docker without sudo =="
sudo usermod -aG docker "$USER"

echo
echo "======================================================================"
echo "IMPORTANT: your shell session does NOT have docker-group membership yet."
echo "Running docker commands (including the next script, 04_deploy_stack.sh)"
echo "in THIS SAME session will fail with a permission error - that is"
echo "expected, not a bug. Fix it one of two ways before continuing:"
echo "  1. Disconnect and reconnect this SSH session, then continue, OR"
echo "  2. Run: newgrp docker    (starts a subshell with the new group active"
echo "     in THIS window - the shell you get afterward is what has it, not"
echo "     any other terminal tab)"
echo "======================================================================"
echo
docker --version
docker compose version
