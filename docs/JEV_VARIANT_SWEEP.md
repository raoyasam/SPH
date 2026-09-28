# Jev variant sweep — monthly momentum

Script: `jev_variant_sweep.py`  
Period: `2021-09-01` → `2026-09-01`, ₹1L start.

## Shared rules

Prior month ≥10% → buy month Open → −10% SL per stock → else month-end Close → churn.  
Costs 0.25%/side; March 20% STCG drag.

## Ranked results (by CAGR)

| Rank | Variant | Final | Return | CAGR | Max DD |
|-----:|---------|------:|-------:|-----:|-------:|
| 1 | **Top10 mom → Top5 by Jev** | ₹3.05L | +205% | **25.0%** | 24.5% |
| 2 | Top10 mom → Top3 by Jev | ₹2.91L | +191% | 23.9% | 25.0% |
| 3 | Weight by Jev (all ≥10%) | ₹2.72L | +172% | 22.2% | 29.5% |
| 4 | No Jev (all ≥10%) | ₹2.70L | +170% | 22.0% | 31.1% |
| 5 | Hybrid veto&lt;0.35 + weight | ₹2.53L | +153% | 20.4% | 29.3% |
| 6 | Soft veto &lt;0.35 | ₹2.47L | +147% | 19.8% | 30.8% |
| 7 | Soft veto &lt;0.40 | ₹2.42L | +142% | 19.3% | 30.6% |
| 8 | Top5 no Jev | ₹2.30L | +130% | 18.1% | 42.9% |
| 9 | Hard filter ≥0.45 | ₹2.10L | +110% | 16.0% | 25.7% |
| 10 | Hard filter ≥0.50 | ₹1.68L | +68% | 11.0% | 22.3% |
| 11 | Hard filter ≥0.55 | ₹0.96L | −4% | −0.9% | 11.6% |

Buy & Hold: +184% / 23.2% CAGR · Nifty 50 +41% · Nifty 500 +61%

## Takeaway

- **Best:** use Jev to **re-rank** Top10 momentum names and keep Top5 (not a hard yes/no gate).  
- **Weight by Jev** slightly beats plain equal-weight.  
- **Hard filters** underperform (cash drag).  
- Soft vetoes are middling.
