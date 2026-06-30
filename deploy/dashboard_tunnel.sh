#!/bin/bash
# Open the VM dashboard in your Mac browser via SSH tunnel (secure — not public).
#
# Usage:
#   bash deploy/dashboard_tunnel.sh
#   open http://127.0.0.1:8765
#
# Keep this terminal open while viewing the dashboard.

set -euo pipefail

VM_NAME="${GCP_VM_NAME:-trading-bot-vm}"
ZONE="${GCP_ZONE:-us-west1-b}"
PORT="${BOT_DASHBOARD_PORT:-8765}"
PROJECT="$(gcloud config get-value project 2>/dev/null)"

if [[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]]; then
  echo "Error: gcloud project not set." >&2
  exit 1
fi

echo "==> Dashboard tunnel to $VM_NAME ($ZONE)"
echo "    Local:  http://127.0.0.1:$PORT"
echo "    Remote: 127.0.0.1:$PORT on VM (bot-dashboard.service)"
echo ""
echo "Ensure dashboard is running on VM:"
echo "  gcloud compute ssh $VM_NAME --zone=$ZONE -- 'sudo systemctl status bot-dashboard'"
echo ""
echo "Press Ctrl+C to close the tunnel."
echo ""

gcloud compute ssh "$VM_NAME" \
  --zone="$ZONE" \
  --project="$PROJECT" \
  -- -N -L "${PORT}:127.0.0.1:${PORT}"
