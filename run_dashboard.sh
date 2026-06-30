#!/bin/bash
# Start live dashboard (web + Cursor canvas, 15s refresh)

set -euo pipefail
cd "$(dirname "$0")"
source bot_env/bin/activate
exec python3 bot_dashboard_server.py
