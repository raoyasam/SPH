# Results-day → 3 sessions / −10% SL

Script: `results_day_3d_backtest.py`

## Rules

1. Yahoo Finance **earnings/results dates** for the equity universe  
2. **Buy** at that day’s **Open** (next session if holiday)  
3. **Exit** at the earlier of:
   - **−10% stop** (fill at entry × 0.90 when day’s Low hits), or  
   - **Close of the 3rd trading session** (entry day = session 1)  
4. Costs 0.25% each side; March 20% STCG drag on realized profit  
5. Sizing: up to `equity / 5` per new trade from available cash  

ETFs skipped (no earnings). Some names have incomplete Yahoo earnings history.

## Run

```bash
python3 results_day_3d_backtest.py
```

## Latest result (2021-01 → 2026-09, ₹1L)

| Strategy | Final | Return | CAGR |
|----------|------:|-------:|-----:|
| Monthly hold (momentum churn) | ₹5.23L | +423% | 33.9% |
| Buy & Hold | ₹3.78L | +278% | 26.4% |
| **Results day → 3d / −10% SL** | **₹0.66L** | **−34%** | **−7.0%** |
| Nifty 50 / 500 | — | +72% / +101% | — |

Trades: 481 | Win rate: 43% | Stops: 28 | Time exits: 453  

## Caveats

- Yahoo earnings dates can be incomplete/wrong for NSE names  
- Daily OHLC stop is approximate (no true intraday fill)  
- Capital often idle between events (slot sizing)  
