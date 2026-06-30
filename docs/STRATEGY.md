# 🤖 Live Bot V2 — Strategy & Operational Manual

**File:** `live_bot_v2.py`  
**Instrument:** NIFTY 50 Options (NSE)  
**Style:** Positional, Always-In Reversal  
**Source Logic:** [ILM Structural Pivots (TradingView)](https://www.tradingview.com/v/9F5oiXJX/)

---

## 🏗️ 1. Core Strategy

### Timeframe
- **15-minute** structural candles (native 15-min historical data from Kite API).
- **Tick-by-tick** breakout detection — executes instantly when price crosses SPH/SPL.
- Pivots recalculate every 15-min boundary (`:00`, `:15`, `:30`, `:45`).

### Pivots (SPH/SPL Only)
Calculated using a **window of 3** (7 total candles: 3 left + center + 3 right):
- **SPH** (Small Pivot High) — Breakout ceiling / Short trailing SL.
- **SPL** (Small Pivot Low) — Breakout floor / Long trailing SL.

> LPH/LPL (major pivots) are **not used** in this version.

### Session-Aware Pivot Calculation (Hybrid)
Pivots are calculated per trading day with session boundaries:

1. **Today's candles:** Uses flexible left bars (up to 3, fewer at session start).
   This allows early-morning candles (e.g., 9:45 AM) to be pivot centers.
2. **Previous session's last 3 candles:** Can use today's candles for right-side
   confirmation (cross-day pivots). Catches late-session pivots that need the
   next day's bars to confirm.
3. **Forward fill:** Yesterday's last confirmed pivot carries forward until a
   new intraday pivot forms today.

> This hybrid approach matches TradingView's behavior where pivots can span
> overnight while keeping intraday pivots accurate.

---

## 🎯 2. Entry & Exit Rules

### Initial Entry (When Flat)
| Condition | Action |
|-----------|--------|
| Tick price **> SPH** | BUY **1 ITM CE** (ATM - 50) |
| Tick price **< SPL** | BUY **1 ITM PE** (ATM + 50) |

### Always-In Reversal (When In Position)
| Current Position | Trigger | Action |
|-----------------|---------|--------|
| **LONG** (holding CE) | Tick **< SPL** | SELL CE → immediately BUY PE (reverse to SHORT) |
| **SHORT** (holding PE) | Tick **> SPH** | SELL PE → immediately BUY CE (reverse to LONG) |

### Trailing Stop Loss
- **Long:** SL = current SPL (auto-updates as new SPL forms).
- **Short:** SL = current SPH (auto-updates as new SPH forms).

> There is **no flat period** — the bot is always in a position once the first signal triggers.

---

## 📊 3. Opening Range Gap Detection (9:15 – 9:18)

### How It Works
1. **9:15-9:17:59** — Collect all ticks, build opening range (High/Low). No execution.
2. **9:18** — Analyze whether market gapped through SPH/SPL:

| Gap Type | 9:18 Tick vs Opening Range | Action |
|----------|---------------------------|--------|
| **Gap DOWN** (low < SPL) | 9:18 LTP **< opening LOW** (new low) | ⚡ Reverse to SHORT |
| **Gap DOWN** (low < SPL) | 9:18 LTP **≥ opening LOW** (holding) | 🛡️ Gap absorbed — keep position |
| **Gap UP** (high > SPH) | 9:18 LTP **> opening HIGH** (new high) | ⚡ Reverse to LONG |
| **Gap UP** (high > SPH) | 9:18 LTP **≤ opening HIGH** (fading) | 🛡️ Gap absorbed — keep position |
| **No gap** | Price within SPH-SPL range | Normal breakout logic |

### Gap Override (for Absorbed Gaps)
When a gap is absorbed, normal SPH/SPL reversal is **suppressed**. The opening range HIGH/LOW becomes temporary breakout levels:

- **SHORT + gap up absorbed:** Keep SHORT. Reverse to LONG only if price breaks **above opening HIGH**.
- **LONG + gap down absorbed:** Keep LONG. Reverse to SHORT only if price breaks **below opening LOW**.
- Once the opening range is broken, the override clears and normal SPH/SPL logic resumes.

> This only affects the **first trade of the day** when a gap scenario occurs.
> All subsequent trades use normal SPH/SPL breakout logic.

---

## 🛒 4. Order Execution

| Parameter | Value |
|-----------|-------|
| **Order Type** | BUY only (no selling/synthetic) |
| **Strike** | 1 ITM (ATM - 50 for CE, ATM + 50 for PE) |
| **Lot Size** | 5 lots = **325 qty** |
| **Expiry** | Next-week (auto-selected, skips if ≤4 days away) |
| **Product** | NRML (positional) |
| **Order Kind** | Market order |

---

## ⏱️ 5. Positional & Expiry Handling

### Overnight Positions
- Positions are held overnight. No forced EOD exit.
- State saved to `bot_v2_state.json` for crash recovery.

### Expiry Day Exit
At **15:15** on the position's expiry day:
1. Force-exit the active position.
2. Automatically refresh instruments for the new next-week expiry.
3. Wait for the next SPH/SPL breakout signal to re-enter.

### Pre-Expiry Rollover (3:20 PM Friday)
When the position's expiry is **≤4 days away** (typically Friday):
1. Triggers at **3:20 PM** (EOD, not morning — avoids morning volatility).
2. Exits current expiry options.
3. Re-enters same direction with next week's expiry.
4. Telegram notification sent.

```
Friday timeline:
  09:18  → Bot trades normally with current expiry
  15:20  → 🔄 Auto-rollover: exit current → re-enter next week
  15:30  → 🌙 Bot shutdown
```

### Daily Shutdown
Bot auto-shuts down at **15:30** every day:
- Saves state to `bot_v2_state.json`.
- Positions remain open (positional strategy).
- Resumes on next manual start.

---

## 🔄 6. Mode Switch

Controlled by the `BOT_MODE` constant at the top of `live_bot_v2.py`:

```python
BOT_MODE = 'SIMULATION'   # Change to 'LIVE' for real orders
```

| Mode | Behavior |
|------|----------|
| `SIMULATION` | Logs all signals and Telegram alerts, but **never places real orders** |
| `LIVE` | Places real market orders via Kite API |

---

## 📱 7. Telegram Integration

### Connection Details
| Parameter | Value |
|-----------|-------|
| **Bot Token** | `your-token-from-BotFather` |
| **Chat ID** | `your-chat-id` |

### Startup Behavior
On startup, the bot **flushes all pending Telegram updates** to prevent stale
`/STOP` or `/ABORT` commands from triggering immediate shutdown.

### Outbound Messages (Bot → You)
| Trigger | Emoji | When |
|---------|-------|------|
| **Bot Started** | 🤖 | On startup — includes mode, strategy, qty, expiry |
| **Opening Range Analysis** | 📊 | At 9:18 — gap detection result |
| **Gap Confirmed/Absorbed** | ⚡/🛡️ | Gap direction decision |
| **Opening Range Break** | ⚡ | When gap override resolves |
| **Position Validated** | ✅ | When carried position confirmed by opening range |
| **New Entry** | 🚀 | On SPH/SPL breakout — includes symbol, price, SL, reason |
| **Trade Exit** | 🛑 | On reversal — includes PnL in ₹ and points |
| **Trailing SL Update** | 🔄 | When SPH/SPL pivot level changes while in position |
| **Status Heartbeat** | ⏱ | Every 15 minutes (only if in a position) |
| **Expiry Rollover** | 📅 | Pre-expiry rollover at 3:20 PM Friday |
| **Daily Shutdown** | 🌙 | At 15:30 — state saved, positions held |
| **Websocket Down** | 🚨 | If no market ticks received for 60+ seconds |
| **Position Closed** | ✅ | After /ABORT force-closes positions |
| **Error Alerts** | ❌ | On order failures, connection errors, etc. |

### Inbound Commands (You → Bot)
| Command | Action |
|---------|--------|
| `/STATUS` | Reply with current position, LTP, unrealized PnL, trailing SL |
| `/STOP` | Graceful shutdown — **positions kept open** (not closed) |
| `/ABORT` | Force-close ALL positions via market order, then shutdown |

---

## ⚙️ 8. Infrastructure

### Crash Recovery
- State saved to `bot_v2_state.json` after every entry, exit, and pivot change.
- On restart, the bot resumes with the same position (validates symbol is still tradeable).

### Watchdog
- If no ticks arrive for 60+ seconds, the bot auto-reconnects the websocket and alerts via Telegram.

### Files Created
| File | Purpose |
|------|---------| 
| `bot_v2_state.json` | Crash-recovery state (positions, pivots, expiry) |
| `bot_v2_execution.log` | Full execution log with timestamps |
| `Completed_Trades_V2.xlsx` | Trade journal with PnL color-coded (green/red) |

---

## 🚀 9. How to Run

```bash
# 1. Ensure access_token.txt exists (from Kite login)
# 2. Set BOT_MODE in live_bot_v2.py ('SIMULATION' or 'LIVE')
# 3. Run:
cd /Users/suhaan/Documents/MyProjects
caffeinate -i python3 live_bot_v2.py
```

---

## 📊 10. What Changed from V1 (live_bot.py)

| Feature | V1 (live_bot.py) | V2 (live_bot_v2.py) |
|---------|-------------------|---------------------|
| Timeframe | 3-min structure | **15-min structure** |
| Pivots | LPH/LPL + SPH/SPL (w=4) | **SPH/SPL only (w=3)** |
| Pivot Data | 1-min resampled | **Native 15-min from Kite** |
| Session Handling | Cross-day window | **Hybrid session-aware** |
| Entry | LPH/LPL breakout (15-min close) | **SPH/SPL breakout (tick-by-tick)** |
| Exit | SPH/SPL trailing SL | **SPH/SPL reversal (always-in)** |
| Gap Handling | None | **Opening range detection (9:15-9:18)** |
| Smart Money Filters | 5 filters (OI, BRN, Velocity, Premium, Sideways) | **None (removed)** |
| Order Type | Synthetic (Sell + Buy legs) | **BUY only (1 ITM)** |
| Strike | ATM ± 250 (5 strikes away) | **ATM ± 50 (1 ITM)** |
| Lots | 2 lots (130 qty) | **5 lots (325 qty)** |
| Style | Intraday (forced EOD exit) | **Positional (hold overnight)** |
| Expiry | Current week (nearest) | **Next week (rollover at 3:20 PM Fri)** |
| Pre-market | No guard | **9:15-9:18 opening range collection** |
| Shutdown | None | **15:30 auto-shutdown** |

---

*Updated: April 17, 2026 | Bot Version: V2.1*
