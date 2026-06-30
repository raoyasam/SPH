#!/bin/bash
# Expose token-protected dashboard on the VM's public IP (port 8765).
#
# Security: link includes ?token=... — treat like a password. HTTP only (no TLS on free VM).
#
# Usage (from Mac):
#   bash deploy/open_dashboard_public.sh
#
# To revert to localhost-only:
#   bash deploy/open_dashboard_public.sh --local

set -euo pipefail

VM_NAME="${GCP_VM_NAME:-trading-bot-vm}"
ZONE="${GCP_ZONE:-us-west1-b}"
PORT="${BOT_DASHBOARD_PORT:-8765}"
PROJECT="$(gcloud config get-value project 2>/dev/null)"
MODE="${1:-public}"

if [[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]]; then
  echo "Error: gcloud project not set." >&2
  exit 1
fi

if [[ "$MODE" == "--local" ]]; then
  PUBLIC=0
  BIND="127.0.0.1"
  echo "==> Reverting dashboard to localhost-only"
else
  PUBLIC=1
  BIND="0.0.0.0"
  echo "==> Opening public dashboard (token-protected) on $VM_NAME"
fi

# GCP firewall (idempotent)
RULE="trading-bot-dashboard"
if [[ "$PUBLIC" == "1" ]]; then
  if ! gcloud compute firewall-rules describe "$RULE" --project="$PROJECT" &>/dev/null; then
    gcloud compute firewall-rules create "$RULE" \
      --project="$PROJECT" \
      --direction=INGRESS \
      --priority=1000 \
      --network=default \
      --action=ALLOW \
      --rules="tcp:${PORT}" \
      --source-ranges=0.0.0.0/0 \
      --target-tags=trading-bot \
      --description="Trading bot dashboard (token auth in app)"
    echo "Created firewall rule: $RULE (tcp:$PORT → trading-bot tag)"
  else
    echo "Firewall rule $RULE already exists"
  fi
fi

gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --command="
set -euo pipefail
INSTALL_DIR=\$HOME/trading-bot
USER=\$(whoami)
SECRETS=\$INSTALL_DIR/bot_secrets.env
PUBLIC=$PUBLIC
BIND='$BIND'

# Ensure dashboard token exists
if ! grep -q '^BOT_DASHBOARD_TOKEN=' \"\$SECRETS\" 2>/dev/null; then
  TOKEN=\$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  echo '' >> \"\$SECRETS\"
  echo '# Dashboard public URL token' >> \"\$SECRETS\"
  echo \"BOT_DASHBOARD_TOKEN='\$TOKEN'\" >> \"\$SECRETS\"
  chmod 600 \"\$SECRETS\"
  echo \"Added BOT_DASHBOARD_TOKEN to bot_secrets.env\"
fi

sed \"s|@USER@|\$USER|g; s|@INSTALL_DIR@|\$INSTALL_DIR|g; s|@DASHBOARD_PUBLIC@|\$PUBLIC|g; s|@DASHBOARD_BIND@|\$BIND|g\" \
  \$INSTALL_DIR/deploy/systemd/bot-dashboard.service | sudo tee /etc/systemd/system/bot-dashboard.service > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now bot-dashboard
sudo systemctl restart bot-dashboard
sleep 2
systemctl is-active bot-dashboard

# Ubuntu UFW blocks everything except SSH by default — must open dashboard port too
if [[ \"\$PUBLIC\" == \"1\" ]]; then
  sudo ufw allow ${PORT}/tcp comment 'bot dashboard' 2>/dev/null || true
else
  sudo ufw delete allow ${PORT}/tcp 2>/dev/null || true
fi
"

NAT_IP="$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT" \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)')"

if [[ "$PUBLIC" == "1" ]]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Dashboard URL:"
  echo ""
  echo "  http://${NAT_IP}:${PORT}/"
  echo ""
  echo "Sign in with BOT_DASHBOARD_TOKEN from bot_secrets.env (password)."
  echo "Session cookie lasts 7 days — token is NOT shown in the URL."
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Use HTTPS + custom domain (e.g. bot.ylx.in) for safer sharing."
else
  echo ""
  echo "Dashboard local on VM only. Use: bash deploy/dashboard_tunnel.sh"
fi
