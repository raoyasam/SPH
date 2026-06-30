# Deploying the trading bot on Google Cloud (GCP)

Unattended operation: **no manual daily login**. The VM uses `KITE_TOTP_SECRET` to generate TOTP codes and refresh the Kite session automatically.

Kite access tokens still expire every morning (~6 AM IST). The setup refreshes them at **8:00 AM IST** (with retries) and again when the bot starts.

---

## Free tier (recommended): e2-micro at $0/month

Google’s **Always Free** tier includes **one e2-micro VM** (1 GB RAM, 2 vCPU shared) in these **US regions only**:

| Region | Zone example | Notes |
|--------|----------------|-------|
| `us-west1` (Oregon) | `us-west1-b` | **Default** — often best latency from India among free regions |
| `us-central1` (Iowa) | `us-central1-a` | Also free |
| `us-east1` (S. Carolina) | `us-east1-b` | Also free |

**Not free:** `asia-south1` (Mumbai) — use paid mode if you need lowest latency.

### Free tier requirements (avoid surprise bills)

| Setting | Required for $0 |
|---------|------------------|
| Machine type | **e2-micro** |
| Region | **us-west1**, **us-central1**, or **us-east1** |
| Boot disk type | **Standard** (`pd-standard`) — not Balanced/SSD |
| Boot disk size | Up to **30 GB** |
| Billing account | Required (charges apply only if you exceed free limits) |

Our `gcp_create_vm.sh` sets all of this by default.

### Trade-offs on e2-micro

- **1 GB RAM** — tight for Python + pandas; `setup_vm.sh` adds **2 GB swap**
- **US region** — ~150–250 ms extra RTT to Kite vs Mumbai; usually fine for 15-min pivot strategy
- **Skip dashboard** on free VM (`bot-dashboard` uses extra RAM); use `journalctl` + Telegram instead
- VM **OS timezone** is still **Asia/Kolkata** (TOTP + market hours correct)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  GCP e2-micro (us-west1-b) · OS timezone Asia/Kolkata        │
├──────────────────────────────────────────────────────────────┤
│  kite-auth.timer  → 8:00 AM IST Mon–Fri → auto TOTP login    │
│  bot-v2.service   → run_bot_v2.sh → live_bot_v2.py         │
│  bot_secrets.env  → KITE_* + TELEGRAM_* (chmod 600)          │
└──────────────────────────────────────────────────────────────┘
```

---

## Quick start (free e2-micro)

### 0. Prerequisites

1. [Google Cloud account](https://console.cloud.google.com) with **billing enabled** (free tier still needs a billing account)
2. [gcloud CLI](https://cloud.google.com/sdk/docs/install) on your Mac

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

### 1. Create free VM (from your Mac)

```bash
bash deploy/gcp_create_vm.sh trading-bot-vm
# Creates e2-micro in us-west1-b, 30GB standard disk — $0 on Always Free

gcloud compute ssh trading-bot-vm --zone=us-west1-b
```

**Paid Mumbai VM** (lower latency, not free):

```bash
GCP_FREE_TIER=0 GCP_ZONE=asia-south1-a bash deploy/gcp_create_vm.sh trading-bot-vm
gcloud compute ssh trading-bot-vm --zone=asia-south1-a
```

### 2. Bootstrap VM (on the VM)

```bash
git clone <your-repo-url> ~/trading-bot
cd ~/trading-bot
bash deploy/setup_vm.sh    # timezone IST + 2GB swap + packages
bash deploy/install.sh     # Python venv + systemd
```

### 3. Upload secrets (from your Mac)

```bash
cd ~/Documents/MyProjects
# Get external IP: gcloud compute instances describe trading-bot-vm --zone=us-west1-b --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
bash deploy/upload_secrets.sh USER@EXTERNAL_IP ~/trading-bot
```

### 4. Test login (on the VM)

```bash
cd ~/trading-bot
source bot_env/bin/activate
python3 kite_auth.py --force
```

### 5. Start services (on the VM)

```bash
sudo systemctl start kite-auth.timer
sudo systemctl start bot-v2
sudo systemctl status bot-v2
journalctl -u bot-v2 -f
```

---

## Daily session flow (no human needed)

| Time (IST) | What happens |
|------------|----------------|
| ~6:00 AM | Kite access token expires |
| 8:00 AM | `kite-auth.timer` + bot → auto TOTP login (retries until success) |
| 8:00–10:00 AM | Bot heartbeat refreshes session if running |
| 9:15 AM | Market open |
| Bot restart | `run_bot_v2.sh` calls `ensure_access_token()` |

---

## Always-on for trade days (from tomorrow)

The VM is configured to run **unattended on every trading day** with no manual steps.

| Component | Behavior |
|-----------|----------|
| **VM** | Stays **RUNNING 24/7** (Mon–Sun). Do **not** stop it on weekends. |
| **bot-v2** | `Restart=always` — starts on boot, auto-restarts on crash |
| **kite-auth.timer** | **Mon–Fri 8:00 AM IST** — daily TOTP login (8 retries) |
| **Bot logic** | Runs continuously; session refresh and trading logic apply on **weekdays** only |

**Why keep the VM running overnight and on weekends?**

1. **Static IP is free only while attached to a running VM.** If you stop the VM but keep `trading-bot-static-ip` reserved, GCP charges ~**$3/month** for an idle IP.
2. **e2-micro Always Free** includes **744 hours/month** — exactly one VM running 24/7 at **$0**.
3. **Zerodha whitelist** stays valid (`YOUR_VM_STATIC_IP`).

### Rules to avoid static IP charges

| Do | Don't |
|----|-------|
| Leave VM **RUNNING** 24/7 | Stop VM and keep static IP reserved |
| Use `bash deploy/check_gcp_health.sh` weekly | Delete VM without releasing or re-attaching the IP |
| Set a GCP billing budget alert (e.g. ₹500) | Create a second e2-micro in the same billing account |

If you ever need to shut down for a long time: either **release** the static IP (and update Zerodha whitelist when you get a new one) or **keep the VM running** — there is no free “stopped VM + reserved IP” option.

### Verify from your Mac

```bash
bash deploy/check_gcp_health.sh
# VM RUNNING + static IP IN_USE + bot-v2 active = good
```

---

## Operations cheat sheet

```bash
sudo systemctl restart bot-v2
journalctl -u bot-v2 -f --since today
bash deploy/refresh_kite_session.sh
systemctl list-timers kite-auth.timer
timedatectl                    # should show Asia/Kolkata
free -h                        # check RAM + swap on e2-micro
```

---

## Live dashboard (trades, PnL, MTM)

The dashboard reads `bot_v2_state.json`, `Completed_Trades_V2.xlsx`, and the bot log. It refreshes every **15 seconds** and fetches **live MTM** for open positions via Kite REST.

| View | Source |
|------|--------|
| Open position + LTP + MTM | state + Kite `ltp()` |
| Today's exits + realized PnL | Excel trade log |
| Cumulative PnL | Excel `Cumulative PnL` column |
| Daily PnL chart | Last 5 session days from exits |
| Stream status | Live / after hours / weekend |

### On the VM

```bash
sudo systemctl enable --now bot-dashboard
sudo systemctl status bot-dashboard
```

Default: **localhost only**. For a **public URL** (token-protected):

```bash
bash deploy/open_dashboard_public.sh
```

Prints the dashboard URL. Sign in with `BOT_DASHBOARD_TOKEN` (password) — no token in the URL.

```bash
bash deploy/open_dashboard_public.sh
# → http://YOUR_VM_STATIC_IP:8765/  (login page)
```

Password is in `bot_secrets.env` as `BOT_DASHBOARD_TOKEN`. After login, a **7-day cookie** keeps you signed in.

Revert to localhost-only:

```bash
bash deploy/open_dashboard_public.sh --local
```

**Security notes**

- HTTP only on the free VM (no HTTPS padlock). Anyone with the link can see PnL/positions.
- Do not share the link on social media or commit it to git.
- Optional: restrict firewall to your home IP instead of `0.0.0.0/0` in GCP Console.

### View from your Mac (SSH tunnel — no public exposure)

```bash
bash deploy/dashboard_tunnel.sh
# In another terminal or browser:
open http://127.0.0.1:8765
```

Keep the tunnel terminal open while viewing. No firewall changes needed.

### Local Mac (bot running locally)

```bash
bash run_dashboard.sh
open http://127.0.0.1:8765
```

Optional Cursor canvas refresh: `BOT_DASHBOARD_CANVAS=1 python3 bot_dashboard_server.py`

---

## Files persisted on VM

- `bot_v2_state.json`
- `Completed_Trades_V2.xlsx`
- `bot_v2_execution.log`
- `access_token.txt`

---

## Security checklist

- [ ] `bot_secrets.env` chmod **600**
- [ ] Firewall: SSH only; no public dashboard port
- [ ] `BOT_MODE = 'SIMULATION'` until verified on VM
- [ ] Set **billing budget alert** in GCP Console (e.g. ₹500) as a safety net

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Dashboard timeout / can't reach IP | GCP firewall + **UFW on VM** must allow tcp:8765. Run `bash deploy/open_dashboard_public.sh` (opens both). |
| OOM / bot killed | Check `free -h`; swap should show ~2G; disable `bot-dashboard` |
| `Kite 2FA failed` | `KITE_TOTP_SECRET` + `timedatectl` (NTP synced) |
| Telegram timeout | `curl -I https://api.telegram.org` from VM |
| WebSocket 403 | `bash deploy/refresh_kite_session.sh` |

---

## Updating the bot

```bash
cd ~/trading-bot && git pull
source bot_env/bin/activate && pip install -r requirements-server.txt -q
sudo systemctl restart bot-v2
```
