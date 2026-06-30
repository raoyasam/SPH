#!/bin/bash
# Install systemd service + timer files (called by deploy/install.sh).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${BOT_INSTALL_DIR:-$REPO_ROOT}"
# Expand ~ for systemd (does not support tilde)
if [[ "$INSTALL_DIR" == "~"* ]]; then
  INSTALL_DIR="${HOME}${INSTALL_DIR#\~}"
fi
BOT_USER="${BOT_USER:-$(whoami)}"
SYSTEMD_DIR="$REPO_ROOT/deploy/systemd"

render() {
  local src="$1"
  local dest="$2"
  sed \
    -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    -e "s|@USER@|$BOT_USER|g" \
    -e "s|@DASHBOARD_PUBLIC@|${DASHBOARD_PUBLIC:-0}|g" \
    -e "s|@DASHBOARD_BIND@|${DASHBOARD_BIND:-127.0.0.1}|g" \
    "$src" | sudo tee "$dest" >/dev/null
}

echo "Installing systemd units for user=$BOT_USER dir=$INSTALL_DIR"

render "$SYSTEMD_DIR/bot-v2.service"        /etc/systemd/system/bot-v2.service
render "$SYSTEMD_DIR/kite-auth.service"   /etc/systemd/system/kite-auth.service
render "$SYSTEMD_DIR/kite-auth.timer"     /etc/systemd/system/kite-auth.timer
render "$SYSTEMD_DIR/bot-dashboard.service" /etc/systemd/system/bot-dashboard.service

sudo systemctl daemon-reload
sudo systemctl enable kite-auth.timer
sudo systemctl enable bot-v2.service

echo "Enabled: kite-auth.timer (daily 8:00 AM IST), bot-v2.service"
echo "Optional dashboard (skip on free e2-micro — low RAM):"
echo "  sudo systemctl enable --now bot-dashboard.service"
