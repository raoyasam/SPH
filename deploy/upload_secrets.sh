#!/bin/bash
# Copy bot_secrets.env from your Mac to the GCP VM (run locally, not on VM).
# Usage: bash deploy/upload_secrets.sh USER@EXTERNAL_IP [remote_path]

set -euo pipefail

LOCAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_SECRETS="$LOCAL_ROOT/bot_secrets.env"
REMOTE="${1:-}"
REMOTE_DIR="${2:-~/trading-bot}"

if [[ -z "$REMOTE" ]]; then
  echo "Usage: bash deploy/upload_secrets.sh USER@VM_IP [remote_install_dir]" >&2
  echo "Example: bash deploy/upload_secrets.sh USER@YOUR_VM_IP ~/trading-bot" >&2
  exit 1
fi

if [[ ! -f "$LOCAL_SECRETS" ]]; then
  echo "Error: $LOCAL_SECRETS not found on this machine." >&2
  exit 1
fi

echo "Uploading bot_secrets.env to $REMOTE:$REMOTE_DIR/ ..."
ssh "$REMOTE" "mkdir -p $REMOTE_DIR"
scp "$LOCAL_SECRETS" "$REMOTE:$REMOTE_DIR/bot_secrets.env"
ssh "$REMOTE" "chmod 600 $REMOTE_DIR/bot_secrets.env"
echo "Done. Secrets file permissions set to 600 on VM."
