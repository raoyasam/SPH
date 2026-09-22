#!/usr/bin/env python3
"""
Same-day Open → Close backtest (Yahoo daily OHLC proxy for 9:30 / 3:15).

Uses the same monthly momentum eligibility as the monthly churn notebook:
  - Prior calendar month return >= 10% => trade those names each day of the next month
  - Each trading day: buy at Open, sell at Close (equal weight)
  - Optional: if day's Low <= Open * 0.90, exit at Open * 0.90 (stop proxy)
  - 0.25% cost each side; 20% STCG drag each March on running profit

Also reports monthly-hold baseline and equal-weight buy & hold for comparison.

Note: Yahoo daily Open/Close are NOT exact 09:30 / 15:15 fills.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf

warnings.simplefilter(action="ignore", category=FutureWarning)

MIN_GROWTH_THRESHOLD = 0.10
TRANSACTION_COST = 0.0025
STCG_TAX_RATE = 0.20
INITIAL_CAPITAL = 100_000.0
START_DATE = "2021-01-01"
END_DATE = "2026-09-01"
STOP_PCT = 0.10

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"
PLOT_PATH = ARTIFACTS / "daily_open_close_comparison.png"
SUMMARY_PATH = ARTIFACTS / "daily_open_close_comparison.json"

STOCKS_MAP = {
    "Aavas": "AAVAS.NS",
    "Angel One": "ANGELONE.NS",
    "Bandhan Bank": "BANDHANBNK.NS",
    "CAMS": "CAMS.NS",
    "CDSL": "CDSL.NS",
    "Epack": "EPACK.NS",
    "Home First": "HOMEFIRST.NS",
    "IDFC First Bank": "IDFCFIRSTB.NS",
    "Jio Fin": "JIOFIN.NS",
    "KFINTECH": "KFINTECH.NS",
    "Mankind": "MANKIND.NS",
    "Max Health": "MAXHEALTH.NS",
    "Minda Corp": "MINDACORP.NS",
    "Nifty Bees": "NIFTYBEES.NS",
    "Poly Medicure": "POLYMED.NS",
    "Rashi Peripherals": "RPTECH.NS",
    "RR Kabel": "RRKABEL.NS",
    "SKY Gold": "SKYGOLD.NS",
    "Uno Minda": "UNOMINDA.NS",
    "Varun Beverages": "VBL.NS",
    "Bank Nifty ETF": "BANKBEES.NS",
    "Bharti Airtel": "BHARTIARTL.NS",
    "HDFC Bank": "HDFCBANK.NS",
    "HDFC Life": "HDFCLIFE.NS",
    "Hindustan Unilever": "HINDUNILVR.NS",
    "IT BEES": "ITBEES.NS",
    "Kotak Bank": "KOTAKBANK.NS",
    "L&T": "LT.NS",
    "Star Health": "STARHEALTH.NS",
    "AU Small Finance Bank": "AUBANK.NS",
    "Groww": "GROWW.NS",
    "HDFC AMC": "HDFCAMC.NS",
    "Syrma SGS": "SYRMA.NS",
    "ASK Auto": "ASKAUTOLTD.NS",
    "Shringar House of Mangalsutra": "SHRINGARMS.NS",
}


def download_universe(start: str, end: str) -> dict[str, pd.DataFrame]:
    all_data: dict[str, pd.DataFrame] = {}
    for name, ticker in STOCKS_MAP.items():
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
        if df is None or df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        all_data[name] = df
    return all_data


def prior_month_return(data: pd.DataFrame, prev_month: pd.Timestamp, curr_month: pd.Timestamp) -> float | None:
    period = data.loc[prev_month : curr_month - pd.Timedelta(days=1)]
    if len(period) <= 1:
        return None
    start_p = float(period["Close"].iloc[0])
    end_p = float(period["Close"].iloc[-1])
    if start_p <= 0:
        return None
    return (end_p - start_p) / start_p


def eligible_names(
    all_data: dict[str, pd.DataFrame], prev_month: pd.Timestamp, curr_month: pd.Timestamp
) -> list[str]:
    out = []
    for name, data in all_data.items():
        ret = prior_month_return(data, prev_month, curr_month)
        if ret is not None and ret >= MIN_GROWTH_THRESHOLD:
            out.append(name)
    return out


def day_net_return(row: pd.Series, use_stop: bool) -> float | None:
    o = float(row["Open"])
    c = float(row["Close"])
    lo = float(row["Low"])
    if o <= 0:
        return None
    exit_px = c
    if use_stop and lo <= o * (1 - STOP_PCT):
        exit_px = o * (1 - STOP_PCT)
    # buy at open after cost, sell at exit after cost
    units_factor = (1 - TRANSACTION_COST)  # capital -> shares notionally
    proceeds_factor = (exit_px / o) * (1 - TRANSACTION_COST)
    return units_factor * proceeds_factor - 1.0


def simulate_monthly_hold(all_data: dict[str, pd.DataFrame]) -> dict:
    """Same rules as monthly churn baseline (hold all month)."""
    portfolio_value = INITIAL_CAPITAL
    annual_running_profit = 0.0
    months = pd.date_range(start=START_DATE, end=END_DATE, freq="MS")
    track_dates = [months[0]]
    track_values = [INITIAL_CAPITAL]

    for i in range(1, len(months)):
        prev_month, curr_month = months[i - 1], months[i]
        names = eligible_names(all_data, prev_month, curr_month)
        if names:
            allocation = portfolio_value / len(names)
            start_cap = portfolio_value
            end_val = 0.0
            for stock in names:
                data = all_data[stock]
                buy_data = data.loc[curr_month : curr_month + pd.Timedelta(days=5)]
                if buy_data.empty:
                    end_val += allocation
                    continue
                buy_price = float(buy_data["Open"].iloc[0])
                units = (allocation * (1 - TRANSACTION_COST)) / buy_price
                stock_data = data.loc[curr_month : curr_month + pd.Timedelta(days=31)]
                exit_price = None
                for _, row in stock_data.iterrows():
                    if (float(row["Close"]) - buy_price) / buy_price <= -STOP_PCT:
                        exit_price = buy_price * (1 - STOP_PCT)
                        break
                if exit_price is None and not stock_data.empty:
                    exit_price = float(stock_data["Close"].iloc[-1])
                if exit_price is None:
                    end_val += allocation
                    continue
                end_val += units * exit_price * (1 - TRANSACTION_COST)
            pnl = end_val - start_cap
            annual_running_profit += pnl
            portfolio_value = end_val

        if curr_month.month == 3:
            if annual_running_profit > 0:
                portfolio_value -= annual_running_profit * STCG_TAX_RATE
            annual_running_profit = 0.0

        track_dates.append(curr_month)
        track_values.append(portfolio_value)

    if annual_running_profit > 0:
        portfolio_value -= annual_running_profit * STCG_TAX_RATE
        track_values[-1] = portfolio_value

    return _summarize("Monthly hold (baseline)", portfolio_value, track_dates, track_values)


def _simulate_daily_with_march_tax(all_data: dict[str, pd.DataFrame], use_stop: bool) -> dict:
    portfolio_value = INITIAL_CAPITAL
    annual_running_profit = 0.0
    months = pd.date_range(start=START_DATE, end=END_DATE, freq="MS")

    sample = next(iter(all_data.values()))
    all_days = sample.index
    for df in all_data.values():
        all_days = all_days.union(df.index)
    all_days = all_days.sort_values()
    all_days = all_days[(all_days >= START_DATE) & (all_days < END_DATE)]

    eligibility_by_month: dict[pd.Timestamp, list[str]] = {}
    for i in range(1, len(months)):
        prev_month, curr_month = months[i - 1], months[i]
        eligibility_by_month[curr_month] = eligible_names(all_data, prev_month, curr_month)

    track_dates = [pd.Timestamp(START_DATE)]
    track_values = [INITIAL_CAPITAL]
    trade_days = 0
    cash_days = 0
    day_returns: list[float] = []
    taxed_years: set[int] = set()

    for idx, day in enumerate(all_days):
        month_key = pd.Timestamp(year=day.year, month=day.month, day=1)
        names = eligibility_by_month.get(month_key, [])
        names = [n for n in names if day in all_data[n].index]

        if not names:
            cash_days += 1
        else:
            rets = []
            for name in names:
                r = day_net_return(all_data[name].loc[day], use_stop=use_stop)
                if r is not None:
                    rets.append(r)
            if not rets:
                cash_days += 1
            else:
                day_ret = sum(rets) / len(rets)
                start_cap = portfolio_value
                portfolio_value *= 1 + day_ret
                annual_running_profit += portfolio_value - start_cap
                day_returns.append(day_ret)
                trade_days += 1

        # Tax on last March trading day of each year
        is_last_march_day = day.month == 3 and (
            idx == len(all_days) - 1 or all_days[idx + 1].month != 3
        )
        if is_last_march_day and day.year not in taxed_years:
            if annual_running_profit > 0:
                portfolio_value -= annual_running_profit * STCG_TAX_RATE
            annual_running_profit = 0.0
            taxed_years.add(day.year)

        track_dates.append(day)
        track_values.append(portfolio_value)

    if annual_running_profit > 0:
        portfolio_value -= annual_running_profit * STCG_TAX_RATE
        track_values[-1] = portfolio_value

    label = "Daily Open→Close + stop" if use_stop else "Daily Open→Close"
    summary = _summarize(label, portfolio_value, track_dates, track_values)
    summary["trade_days"] = trade_days
    summary["cash_days"] = cash_days
    summary["win_rate_pct"] = round(100.0 * sum(1 for r in day_returns if r > 0) / len(day_returns), 2) if day_returns else 0.0
    summary["avg_day_return_pct"] = round(100.0 * (sum(day_returns) / len(day_returns)), 4) if day_returns else 0.0
    return summary


def buy_and_hold(all_data: dict[str, pd.DataFrame]) -> dict:
    valid = [n for n, d in all_data.items() if not d.empty]
    alloc = INITIAL_CAPITAL / len(valid)
    end_val = 0.0
    for name in valid:
        data = all_data[name]
        units = alloc / float(data["Open"].iloc[0])
        end_val += units * float(data["Close"].iloc[-1])
    return _summarize("Buy & Hold (equal weight)", end_val, [pd.Timestamp(START_DATE), pd.Timestamp(END_DATE)], [INITIAL_CAPITAL, end_val])


def benchmark_return(ticker: str) -> float:
    df = yf.download(ticker, start=START_DATE, end=END_DATE, progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    return ((float(df[col].iloc[-1]) - float(df[col].iloc[0])) / float(df[col].iloc[0])) * 100


def _summarize(label: str, final_value: float, track_dates, track_values) -> dict:
    years = (pd.to_datetime(END_DATE) - pd.to_datetime(START_DATE)).days / 365.25
    total_return = ((final_value - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    cagr = (((final_value / INITIAL_CAPITAL) ** (1 / years)) - 1) * 100 if years > 0 and final_value > 0 else -100.0
    return {
        "label": label,
        "final_value": round(final_value, 2),
        "total_return_pct": round(total_return, 2),
        "cagr_pct": round(cagr, 2),
        "track_dates": [pd.Timestamp(d).isoformat() for d in track_dates],
        "track_values": [float(v) for v in track_values],
    }


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    print(f"Downloading universe ({len(STOCKS_MAP)} tickers) {START_DATE} -> {END_DATE} ...")
    all_data = download_universe(START_DATE, END_DATE)
    print(f"Loaded {len(all_data)} series.")

    monthly = simulate_monthly_hold(all_data)
    daily = _simulate_daily_with_march_tax(all_data, use_stop=False)
    daily_stop = _simulate_daily_with_march_tax(all_data, use_stop=True)
    bah = buy_and_hold(all_data)
    nifty50 = round(benchmark_return("^NSEI"), 2)
    nifty500 = round(benchmark_return("^CRSLDX"), 2)

    def slim(d: dict) -> dict:
        return {k: v for k, v in d.items() if k not in ("track_dates", "track_values")}

    summary = {
        "period": f"{START_DATE} to {END_DATE}",
        "initial_capital": INITIAL_CAPITAL,
        "note": "Daily Open/Close from Yahoo is a proxy for ~9:30 / ~15:15, not exact intraday fills.",
        "monthly_hold": slim(monthly),
        "daily_open_close": slim(daily),
        "daily_open_close_with_stop": slim(daily_stop),
        "buy_and_hold": slim(bah),
        "nifty50_total_return_pct": nifty50,
        "nifty500_total_return_pct": nifty500,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    plt.figure(figsize=(12, 6))
    for run, style in (
        (monthly, "-"),
        (daily, "-"),
        (daily_stop, "--"),
    ):
        plt.plot(pd.to_datetime(run["track_dates"]), run["track_values"], style, label=run["label"])
    plt.title("Monthly hold vs same-day Open→Close (Yahoo OHLC proxy)")
    plt.xlabel("Date")
    plt.ylabel("Portfolio value (INR)")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(PLOT_PATH, dpi=120)
    plt.close()

    rows = [
        slim(monthly),
        slim(daily),
        slim(daily_stop),
        slim(bah),
    ]
    print("\n" + "=" * 72)
    print("COMPARISON SUMMARY")
    print("=" * 72)
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"\nNifty 50 total return:  {nifty50:.2f}%")
    print(f"Nifty 500 total return: {nifty500:.2f}%")
    print(f"Saved: {SUMMARY_PATH}")
    print(f"Plot:  {PLOT_PATH}")
    print("=" * 72)


if __name__ == "__main__":
    main()
