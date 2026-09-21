# Monthly churn vs TypeSafe Jev

Backtest script: `monthly_churn_jev_backtest.py`

## What it compares

Same monthly momentum rules as the notebook:

- Prior month return ≥ 10% → eligible
- Equal-weight buy next month open
- −10% stop or month-end exit
- 0.25% costs, 20% March STCG drag

**Jev variant:** after eligibility, ask TypeSafe Jev (`approve_buy` Noul). Keep names with score ≥ threshold.

## Run

```bash
pip install -r requirements-churn.txt
# TYPESAFE_API_KEY in bot_secrets.env
python3 monthly_churn_jev_backtest.py
```

Outputs under `artifacts/`:

- `monthly_churn_comparison.json`
- `monthly_churn_baseline_vs_jev.png`
- `jev_monthly_cache.json` (gitignored; avoids re-billing API)

## Latest result (2021-01-01 → 2026-09-01, ₹1L start)

| Strategy | Final | Return | CAGR |
|----------|------:|-------:|-----:|
| Baseline (no Jev) | ₹5.23L | +423% | 33.9% |
| Jev ≥ 0.45 | ₹3.15L | +215% | 22.5% |
| Jev ≥ 0.50 | ₹2.02L | +102% | 13.2% |
| Jev ≥ 0.55 | ₹0.94L | −6% | −1.2% |
| Equal-weight B&H | ₹3.59L | +259% | 25.3% |
| Nifty 50 | — | +72% | — |
| Nifty 500 | — | +101% | — |

**Takeaway:** On this curated universe/period, the plain monthly churn beat Jev filters. Higher Jev thresholds sat in cash too often. Treat Jev scores as *retrospective* (today’s model on past months), not a true historical walk-forward.
