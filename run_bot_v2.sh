#!/bin/bash
# Run structural pivot bot v2. All secrets in bot_secrets.env (see bot_secrets.env.example)

set -euo pipefail
cd "$(dirname "$0")"

source bot_env/bin/activate

if [[ -f bot_secrets.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source bot_secrets.env
  set +a
elif [[ -f kite_secrets.env ]] || [[ -f telegram_secrets.env ]]; then
  # Legacy: load split env files until you migrate to bot_secrets.env
  set -a
  [[ -f kite_secrets.env ]] && source kite_secrets.env
  [[ -f telegram_secrets.env ]] && source telegram_secrets.env
  set +a
  echo "Note: migrate to a single bot_secrets.env (see bot_secrets.env.example)" >&2
else
  echo "Error: bot_secrets.env not found." >&2
  echo "  cp bot_secrets.env.example bot_secrets.env && edit with your keys." >&2
  exit 1
fi

for var in KITE_API_KEY KITE_API_SECRET KITE_USER_ID KITE_PASSWORD KITE_TOTP_SECRET \
           TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
  if [[ -z "${!var:-}" ]]; then
    echo "Error: $var is not set in bot_secrets.env" >&2
    exit 1
  fi
done

echo "Refreshing Kite session (reuses token if still valid)..."
python3 -c "from kite_auth import ensure_access_token; ensure_access_token()"

echo "Starting Live Bot v2..."
if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i python3 live_bot_v2.py
else
  exec python3 live_bot_v2.py
fi
