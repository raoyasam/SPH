#!/bin/bash
# One-time bootstrap for Ubuntu 22.04/24.04 on GCP (or any Linux VM).
# Run as your deploy user (not root): bash deploy/setup_vm.sh

set -euo pipefail

echo "==> Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
  python3 \
  python3-venv \
  python3-pip \
  git \
  curl \
  chrony \
  ufw

echo "==> Setting timezone to Asia/Kolkata (required for TOTP + market hours)..."
sudo timedatectl set-timezone Asia/Kolkata
timedatectl

echo "==> Ensuring NTP/chrony is running (TOTP fails if clock drifts)..."
sudo systemctl enable --now chrony

# e2-micro has only 1 GB RAM — add swap so pandas + bot don't OOM
if [[ "${SETUP_SWAP:-1}" == "1" ]] && ! swapon --show 2>/dev/null | grep -q .; then
  echo "==> Adding 2 GB swap (recommended for e2-micro free tier)..."
  if [[ ! -f /swapfile ]]; then
    sudo fallocate -l 2G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
  fi
  sudo swapon /swapfile 2>/dev/null || true
  if ! grep -q '^/swapfile ' /etc/fstab 2>/dev/null; then
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  fi
  free -h
fi

echo "==> Basic firewall: SSH only (dashboard stays local unless you open port 8765)..."
sudo ufw allow OpenSSH
sudo ufw --force enable
sudo ufw status

echo ""
echo "VM bootstrap complete."
echo "Next: clone/copy the bot repo, then run:  bash deploy/install.sh"
