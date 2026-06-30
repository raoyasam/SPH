# SPH — NIFTY Structural Pivot Trading Bot (v2)

Automated NIFTY options bot using **15-minute SPH/SPL** structural pivots with always-in reversal logic. Integrates with **Zerodha Kite Connect**, **Telegram** alerts/commands, and optional **GCP** deployment.

> **Disclaimer:** For educational purposes. Live trading involves real financial risk. Test in `SIMULATION` mode first. You are responsible for your own trades and compliance with broker/exchange rules.

## Features

- 15-min SPH/SPL pivot calculation with gap-opening-range logic
- Positional always-in strategy (LONG ↔ SHORT reversals on structure)
- `SIMULATION` (paper) and `LIVE` modes
- Automated Kite login via TOTP (`kite_auth.py`)
- Telegram: `/STATUS`, `/STOP`, `/ABORT`, `/LOTS`, `/CONFIG`, etc.
- Optional web dashboard (MTM, PnL, trade log)
- GCP VM deploy scripts + systemd services

## Prerequisites

1. [Zerodha](https://zerodha.com) account with **Kite Connect** API app ([developers.kite.trade](https://developers.kite.trade))
2. Kite app redirect URL must include: `http://127.0.0.1`
3. [Telegram bot](https://t.me/BotFather) + your chat ID
4. Python 3.10+ (3.12 recommended)
5. For cloud deploy: Google Cloud account (optional)

## Quick start (local)

```bash
git clone https://github.com/raoyasam/SPH.git
cd SPH

python3 -m venv bot_env
source bot_env/bin/activate
pip install -r requirements.txt

cp bot_secrets.env.example bot_secrets.env
# Edit bot_secrets.env with your Kite + Telegram credentials

# Test Kite login
python3 kite_auth.py --force

# Run in SIMULATION (default in repo — no real orders)
bash run_bot_v2.sh
```

### Go LIVE (real orders)

1. In `live_bot_v2.py`, set `BOT_MODE = 'LIVE'`
2. Set `NUM_LOTS` to your desired size (1 lot = 65 qty for NIFTY)
3. Ensure Zerodha **static IP whitelist** includes your machine/VM IP
4. Restart the bot

**Never commit `bot_secrets.env` or `access_token.txt`.**

## Configuration

| File | Purpose |
|------|---------|
| `live_bot_v2.py` | Strategy config (`BOT_MODE`, `NUM_LOTS`, `ORDER_TYPE`, …) |
| `bot_secrets.env` | Kite + Telegram + dashboard token (local only) |
| `bot_secrets.env.example` | Template for credentials |

See [docs/STRATEGY.md](docs/STRATEGY.md) for strategy details and [deploy/DEPLOY.md](deploy/DEPLOY.md) for GCP setup.

## Dashboard (optional)

```bash
source bot_env/bin/activate
bash run_dashboard.sh
# http://127.0.0.1:8765/
```

Set `BOT_DASHBOARD_TOKEN` in `bot_secrets.env` for login. See `deploy/DEPLOY.md` for public VM access.

## Telegram commands

| Command | Action |
|---------|--------|
| `/STATUS` | Position, SL, unrealized PnL |
| `/STOP` | Shutdown bot (keeps positions) |
| `/ABORT` | Close all positions + shutdown (**LIVE only**) |
| `/LOTS N` | Change lot size for next entry |
| `/CONFIG` | Show current settings |

## Project layout

```
live_bot_v2.py          # Main bot
kite_auth.py            # Automated Kite TOTP login
bot_secrets.py          # Env loader
run_bot_v2.sh           # Startup script
bot_dashboard_*.py      # Optional dashboard
deploy/                 # GCP + systemd install scripts
docs/STRATEGY.md        # Strategy documentation
```

## License

Use at your own risk. No warranty.
