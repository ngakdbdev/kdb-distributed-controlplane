#!/usr/bin/env bash
# 03_install_docker.sh - run this ON the VM (SSH in first), or remotely via:
#   gcloud compute ssh <vm-name> --zone <zone> --command "$(cat 03_install_docker.sh)"
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

echo "== done - log out and back in (or run 'newgrp docker') for group membership to take effect =="
docker --version
docker compose version
