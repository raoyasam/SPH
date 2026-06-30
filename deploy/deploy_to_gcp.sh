#!/bin/bash
# End-to-end deploy: create free e2-micro VM + install bot + upload secrets + start services.
#
# Prerequisites:
#   1. gcloud auth login
#   2. Billing enabled on your GCP project (required even for free tier)
#   3. bot_secrets.env filled in locally
#
# Usage:
#   export PATH="$HOME/google-cloud-sdk/bin:$PATH"
#   gcloud config set project YOUR_PROJECT_ID
#   bash deploy/deploy_to_gcp.sh
#
# Optional env:
#   VM_NAME=trading-bot-vm
#   GCP_ZONE=us-west1-b
#   GCP_PROJECT=your-project-id

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_NAME="${VM_NAME:-trading-bot-vm}"
ZONE="${GCP_ZONE:-us-west1-b}"
PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REMOTE_DIR="~/trading-bot"
LOCAL_SECRETS="$REPO_ROOT/bot_secrets.env"

# Force Always Free tier — refuse paid regions/types unless explicitly overridden
export GCP_FREE_TIER="${GCP_FREE_TIER:-1}"
if [[ "$GCP_FREE_TIER" != "1" ]]; then
  die "Paid deploy disabled. This script only deploys free-tier e2-micro in us-west1.
  Unset GCP_FREE_TIER=0 or use gcp_create_vm.sh directly if you really want paid."
fi
export GCP_ZONE="$ZONE"

export PATH="${HOME}/google-cloud-sdk/bin:${PATH}"

die() { echo "Error: $*" >&2; exit 1; }

command -v gcloud >/dev/null || die "gcloud not found. Install Google Cloud CLI first."

[[ -n "$PROJECT" && "$PROJECT" != "(unset)" ]] || die "No GCP project. Run: gcloud config set project YOUR_PROJECT_ID"
[[ -f "$LOCAL_SECRETS" ]] || die "bot_secrets.env not found at $LOCAL_SECRETS"

echo "==> Project: $PROJECT"
echo "==> VM:      $VM_NAME ($ZONE, e2-micro free tier)"
echo "==> Repo:    $REPO_ROOT"
echo ""

echo "==> Enabling Compute Engine API (if needed)..."
if ! gcloud services enable compute.googleapis.com --project="$PROJECT" 2>&1; then
  echo ""
  die "Compute API could not be enabled. Link billing at:
  https://console.cloud.google.com/billing/linkedaccount?project=$PROJECT"
fi

if gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT" &>/dev/null; then
  echo "==> VM '$VM_NAME' already exists — skipping create"
else
  echo "==> Creating e2-micro VM..."
  bash "$REPO_ROOT/deploy/gcp_create_vm.sh" "$VM_NAME"
fi

echo "==> Waiting for VM to be SSH-ready..."
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --command="echo VM ready" 2>/dev/null \
  || sleep 15

echo "==> Syncing bot code to VM (excluding secrets, venv, logs)..."
gcloud compute scp --recurse --zone="$ZONE" --project="$PROJECT" \
  "$REPO_ROOT/live_bot_v2.py" \
  "$REPO_ROOT/kite_auth.py" \
  "$REPO_ROOT/bot_secrets.py" \
  "$REPO_ROOT/bot_dashboard_data.py" \
  "$REPO_ROOT/bot_dashboard_canvas.py" \
  "$REPO_ROOT/bot_dashboard_server.py" \
  "$REPO_ROOT/zerodha_token.py" \
  "$REPO_ROOT/run_bot_v2.sh" \
  "$REPO_ROOT/run_dashboard.sh" \
  "$REPO_ROOT/requirements-server.txt" \
  "$REPO_ROOT/bot_secrets.env.example" \
  "$VM_NAME:~/trading-bot-tmp/" 2>/dev/null || true

# Rsync via tar is more reliable for full deploy folder
tar -C "$REPO_ROOT" -czf /tmp/trading-bot-deploy.tgz \
  --exclude='bot_env' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='bot_secrets.env' \
  --exclude='*.log' \
  --exclude='.git' \
  --exclude='SPL-SPH strategy' \
  --exclude='bot_env' \
  live_bot_v2.py kite_auth.py bot_secrets.py bot_dashboard_data.py \
  bot_dashboard_canvas.py bot_dashboard_server.py zerodha_token.py \
  run_bot_v2.sh run_dashboard.sh requirements-server.txt requirements.txt \
  bot_secrets.env.example deploy/

gcloud compute scp --zone="$ZONE" --project="$PROJECT" \
  /tmp/trading-bot-deploy.tgz "$VM_NAME:/tmp/trading-bot-deploy.tgz"

gcloud compute scp --zone="$ZONE" --project="$PROJECT" \
  "$LOCAL_SECRETS" "$VM_NAME:/tmp/bot_secrets.env"

echo "==> Running install on VM..."
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --command="
  set -euo pipefail
  mkdir -p $REMOTE_DIR
  tar -xzf /tmp/trading-bot-deploy.tgz -C $REMOTE_DIR
  mv /tmp/bot_secrets.env $REMOTE_DIR/bot_secrets.env
  chmod 600 $REMOTE_DIR/bot_secrets.env
  cd $REMOTE_DIR
  if [[ ! -f .vm_bootstrapped ]]; then
    bash deploy/setup_vm.sh
    touch .vm_bootstrapped
  fi
  bash deploy/install.sh
  source bot_env/bin/activate
  python3 kite_auth.py --force
  sudo systemctl enable kite-auth.timer
  sudo systemctl start kite-auth.timer
  sudo systemctl enable bot-v2.service
  sudo systemctl restart bot-v2
  sleep 3
  sudo systemctl is-active bot-v2
  journalctl -u bot-v2 -n 15 --no-pager
"

EXTERNAL_IP="$(gcloud compute instances describe "$VM_NAME" \
  --zone="$ZONE" --project="$PROJECT" \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)')"

echo ""
echo "=========================================="
echo "DEPLOY COMPLETE"
echo "=========================================="
echo "VM:         $VM_NAME"
echo "Zone:       $ZONE"
echo "External IP: $EXTERNAL_IP"
echo ""
echo "SSH:        gcloud compute ssh $VM_NAME --zone=$ZONE"
echo "Bot logs:   gcloud compute ssh $VM_NAME --zone=$ZONE -- journalctl -u bot-v2 -f"
echo "Restart:    gcloud compute ssh $VM_NAME --zone=$ZONE -- sudo systemctl restart bot-v2"
echo "=========================================="
