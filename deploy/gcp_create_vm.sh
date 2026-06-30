#!/bin/bash
# Create a GCP Compute Engine VM for the trading bot.
#
# Default: Always Free tier (e2-micro, US region, standard 30GB disk).
# Mumbai/low-latency paid VM:  GCP_FREE_TIER=0 bash deploy/gcp_create_vm.sh
#
# Prerequisites: gcloud CLI + billing account linked (free tier still requires billing).
#
# Usage: bash deploy/gcp_create_vm.sh [VM_NAME]

set -euo pipefail

VM_NAME="${1:-trading-bot-vm}"
FREE_TIER="${GCP_FREE_TIER:-1}"
PROJECT="$(gcloud config get-value project 2>/dev/null)"

if [[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]]; then
  echo "Error: gcloud project not set. Run: gcloud config set project YOUR_PROJECT_ID" >&2
  exit 1
fi

if [[ "$FREE_TIER" == "1" ]]; then
  # Always Free: e2-micro in us-west1 | us-central1 | us-east1 only
  ZONE="${GCP_ZONE:-us-west1-b}"
  MACHINE_TYPE="e2-micro"
  BOOT_DISK_SIZE="30GB"
  BOOT_DISK_TYPE="pd-standard"
  NETWORK_TIER="STANDARD"
  TIER_LABEL="Always Free (e2-micro)"
else
  ZONE="${GCP_ZONE:-asia-south1-a}"
  MACHINE_TYPE="${GCP_MACHINE_TYPE:-e2-small}"
  BOOT_DISK_SIZE="20GB"
  BOOT_DISK_TYPE="pd-balanced"
  NETWORK_TIER="PREMIUM"
  TIER_LABEL="Paid ($MACHINE_TYPE, low latency India)"
fi

echo "==> Creating VM: $VM_NAME"
echo "    Project:  $PROJECT"
echo "    Tier:     $TIER_LABEL"
echo "    Zone:     $ZONE"
echo "    Machine:  $MACHINE_TYPE"
echo "    Disk:     $BOOT_DISK_SIZE $BOOT_DISK_TYPE"
echo ""

gcloud compute instances create "$VM_NAME" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size="$BOOT_DISK_SIZE" \
  --boot-disk-type="$BOOT_DISK_TYPE" \
  --network-tier="$NETWORK_TIER" \
  --tags=trading-bot \
  --metadata=enable-oslogin=TRUE \
  --labels=app=trading-bot,tier="$([[ "$FREE_TIER" == "1" ]] && echo free || echo paid)"

echo ""
echo "VM created."
echo ""
if [[ "$FREE_TIER" == "1" ]]; then
  echo "Free tier notes:"
  echo "  - VM is in the US (us-west1). Clock uses Asia/Kolkata on the OS; ~200ms extra latency to Kite vs Mumbai."
  echo "  - 1 GB RAM: setup_vm.sh adds 2 GB swap. Skip dashboard on this VM."
  echo "  - Stays free if: e2-micro + pd-standard disk + US region + within monthly hours."
  echo "  - Billing account required but this instance should cost \$0/month on Always Free."
  echo ""
fi
echo "SSH:"
echo "  gcloud compute ssh $VM_NAME --zone=$ZONE"
echo ""
echo "On the VM:"
echo "  git clone <your-repo-url> ~/trading-bot"
echo "  cd ~/trading-bot && bash deploy/setup_vm.sh && bash deploy/install.sh"
echo ""
echo "Upload secrets from Mac:"
echo "  bash deploy/upload_secrets.sh <USER>@<EXTERNAL_IP> ~/trading-bot"
