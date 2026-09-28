# Nifty 500 universe — monthly momentum +/- Jev

Script: `nifty500_monthly_jev_backtest.py`  
Universe: **current** NSE Nifty 500 list (Yahoo `.NS`) — **survivorship bias** vs historical membership.

Period: `2021-09-01` → `2026-09-01`, ₹1L start.  
Rules: prior month ≥10%, buy Open, −10% SL / month-end, costs + March tax (same as before).

## Results

| Variant | Final | Return | CAGR | Max DD |
|---------|------:|-------:|-----:|-------:|
| **Top5 momentum (no Jev)** | **₹3.10L** | **+210%** | **25.4%** | 36.9% |
| All ≥10% equal weight (no Jev) | ₹2.66L | +166% | 21.7% | 14.7% |
| Top10 mom → Top5 by Jev | ₹2.12L | +112% | 16.2% | 32.1% |
| Nifty 50 / Nifty 500 index | — | +41% / +61% | — | — |

Avg names when “all ≥10%”: ~**77**/month.

## vs curated ~35-name list (same period)

| Variant | Curated list CAGR | Nifty 500 CAGR |
|---------|------------------:|---------------:|
| Top5 no Jev | 18.1% | **25.4%** |
| All ≥10% no Jev | 22.0% | 21.7% |
| Top10→Top5 Jev | **25.0%** | 16.2% |

**Takeaway:** On the broad Nifty 500 set, plain **Top5 momentum (no Jev)** wins. Jev re-rank that helped on the curated list **hurts** here.
