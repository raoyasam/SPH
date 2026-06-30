#!/bin/bash
# Refresh Kite access token (auto-login with TOTP). Used by systemd timer + manual runs.

set -euo pipefail

INSTALL_DIR="${BOT_INSTALL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$INSTALL_DIR"

# shellcheck disable=SC1091
source "$INSTALL_DIR/bot_env/bin/activate"

if [[ -f "$INSTALL_DIR/bot_secrets.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$INSTALL_DIR/bot_secrets.env"
  set +a
else
  echo "Error: bot_secrets.env missing at $INSTALL_DIR/bot_secrets.env" >&2
  exit 1
fi

echo "$(date '+%F %T %Z') — refreshing Kite session..."
python3 "$INSTALL_DIR/kite_auth.py" --force
echo "$(date '+%F %T %Z') — done"
