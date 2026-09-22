# Daily Open → Close backtest (Yahoo OHLC proxy)

Script: `daily_open_close_backtest.py`

## Idea

Approximate same-day trading (want 9:30 → 15:15) using Yahoo **daily Open → Close**.

Eligibility matches the monthly churn notebook: prior month return ≥ 10%, then trade those names **every day** of the next month (equal weight).

## Run

```bash
pip install -r requirements-churn.txt
python3 daily_open_close_backtest.py
```

## Latest result (2021-01 → 2026-09, ₹1L)

| Strategy | Final | Return | CAGR |
|----------|------:|-------:|-----:|
| Monthly hold (baseline) | ₹5.23L | +423% | 33.9% |
| Daily Open→Close @ 0.25% cost/side | ~₹34 | −99.97% | −75.5% |
| Daily Open→Close + −10% stop | ~₹29 | −99.97% | −76.3% |
| Buy & Hold | ₹3.59L | +259% | 25.3% |

### Cost sensitivity (daily Open→Close, no stop)

| Cost each side | Final | Return | Avg day |
|----------------|------:|-------:|--------:|
| 0.00% | ₹16.8k | −83% | −0.13% |
| 0.05% | ₹4.9k | −95% | −0.23% |
| 0.10% | ₹1.4k | −99% | −0.33% |
| 0.25% | ₹34 | −100% | −0.63% |

Even **zero cost** loses: the ≥10% prior-month names had a negative average Open→Close drift on this sample (~−0.13%/day, win rate ~44%).

## Caveats

- Open/Close ≠ exact 09:30 / 15:15
- 0.25% each side was copied from the monthly model; realistic discount brokerage is lower, but even then daily churn is hard
- Monthly hold still wins by a wide margin on this universe/period
