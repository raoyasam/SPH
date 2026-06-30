#!/bin/bash
# Install bot Python env + systemd services on the VM.
# Run from repo root: bash deploy/install.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${BOT_INSTALL_DIR:-$REPO_ROOT}"
VENV_DIR="$INSTALL_DIR/bot_env"

cd "$INSTALL_DIR"

echo "==> Install directory: $INSTALL_DIR"

if [[ ! -f "$INSTALL_DIR/live_bot_v2.py" ]]; then
  echo "Error: live_bot_v2.py not found in $INSTALL_DIR" >&2
  exit 1
fi

echo "==> Creating Python virtualenv..."
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r "$INSTALL_DIR/requirements-server.txt" -q

if [[ ! -f "$INSTALL_DIR/bot_secrets.env" ]]; then
  echo ""
  echo "WARNING: bot_secrets.env not found."
  echo "  Copy from your Mac:  bash deploy/upload_secrets.sh USER@VM_IP"
  echo "  Or manually:         cp bot_secrets.env.example bot_secrets.env"
  echo ""
fi

echo "==> Making scripts executable..."
chmod +x "$INSTALL_DIR/run_bot_v2.sh" \
  "$INSTALL_DIR/run_dashboard.sh" \
  "$INSTALL_DIR/deploy/"*.sh 2>/dev/null || true

echo "==> Installing systemd units..."
BOT_INSTALL_DIR="$INSTALL_DIR" BOT_USER="${BOT_USER:-$(whoami)}" bash "$INSTALL_DIR/deploy/install_systemd.sh"

echo ""
echo "Install complete."
echo ""
echo "Before starting:"
echo "  1. Ensure bot_secrets.env exists with all KITE_* and TELEGRAM_* keys"
echo "  2. Test login:  source bot_env/bin/activate && python3 kite_auth.py --force"
echo ""
echo "Start bot:     sudo systemctl start bot-v2"
echo "Bot logs:      journalctl -u bot-v2 -f"
echo "Dashboard:     sudo systemctl start bot-dashboard  (optional)"
echo "               ssh -L 8765:127.0.0.1:8765 USER@VM  then open http://127.0.0.1:8765"
