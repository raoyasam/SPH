# Top-5 monthly momentum — 5y with/without Jev

Script: `top5_monthly_jev_5y_backtest.py`

## Rules

- Prior month return ≥ 10%, rank descending  
- **Top 5** equal-weight (or fewer if &lt;5 eligible)  
- Buy next month Open; −10% stop or month-end Close  
- 0.25% costs; 20% March STCG drag  

### Variants

| Variant | Selection |
|---------|-----------|
| Top5 no Jev | Top 5 by momentum |
| Top5 + Jev filter | Top 5 by momentum, keep only `noul ≥ 0.50` |
| Top10 → Top5 by Jev | Top 10 by momentum, re-rank by Jev, take Top 5 |

## Period

`2021-09-01` → `2026-09-01` (≈5 years), ₹1L start.

## Results

| Strategy | Final | Return | CAGR |
|----------|------:|-------:|-----:|
| Top5 momentum (no Jev) | ₹2.30L | +130% | 18.1% |
| Top5 then Jev filter (≥0.50) | ₹1.43L | +43% | 7.4% |
| **Top10 mom → Top5 by Jev** | **₹3.05L** | **+205%** | **25.0%** |
| Buy & Hold | ₹2.84L | +184% | 23.2% |
| Nifty 50 / 500 | — | +41% / +61% | — |

**Takeaway:** Hard Jev veto hurts (too much cash). **Re-ranking** Top10 momentum names by Jev and keeping Top5 beat plain Top5 and B&H on this sample.
