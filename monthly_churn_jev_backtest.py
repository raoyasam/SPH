#!/usr/bin/env python3
"""
Monthly momentum churn backtest: baseline vs TypeSafe Jev filter.

Baseline rules (unchanged):
  - Prior calendar month return >= 10% => eligible next month
  - Equal-weight buy at next month open
  - Exit on -10% stop (fill at buy*0.90) or month-end close
  - 0.25% cost each side; 20% STCG drag each March on running profit

Jev variant:
  - Same eligibility, then ask Jev (Noul) whether to approve the buy
  - Keep only names with approve_buy >= JEV_THRESHOLD
  - If all rejected => sit in cash for that month
"""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf

from bot_secrets import load_secrets, require_env

warnings.simplefilter(action="ignore", category=FutureWarning)

# --- Config ---
MIN_GROWTH_THRESHOLD = 0.10
TRANSACTION_COST = 0.0025
STCG_TAX_RATE = 0.20
INITIAL_CAPITAL = 100_000.0
START_DATE = "2021-01-01"
END_DATE = "2026-09-01"
JEV_THRESHOLD = 0.55
JEV_MODEL = "jev-latest"

ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "artifacts" / "jev_monthly_cache.json"
PLOT_PATH = ROOT / "artifacts" / "monthly_churn_baseline_vs_jev.png"
SUMMARY_PATH = ROOT / "artifacts" / "monthly_churn_comparison.json"

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


def eligible_names(all_data: dict[str, pd.DataFrame], prev_month: pd.Timestamp, curr_month: pd.Timestamp) -> list[str]:
    out = []
    for name, data in all_data.items():
        ret = prior_month_return(data, prev_month, curr_month)
        if ret is not None and ret >= MIN_GROWTH_THRESHOLD:
            out.append(name)
    return out


def simulate_month(
    all_data: dict[str, pd.DataFrame],
    names: list[str],
    portfolio_value: float,
    curr_month: pd.Timestamp,
) -> tuple[float, float]:
    """Return (new_portfolio_value, monthly_pnl)."""
    if not names:
        return portfolio_value, 0.0

    allocation = portfolio_value / len(names)
    month_start = portfolio_value
    month_end_value = 0.0

    for stock in names:
        data = all_data[stock]
        buy_data = data.loc[curr_month : curr_month + pd.Timedelta(days=5)]
        if buy_data.empty:
            month_end_value += allocation
            continue

        buy_price = float(buy_data["Open"].iloc[0])
        units = (allocation * (1 - TRANSACTION_COST)) / buy_price

        stock_data = data.loc[curr_month : curr_month + pd.Timedelta(days=31)]
        exit_price = None
        for _, row in stock_data.iterrows():
            if (float(row["Close"]) - buy_price) / buy_price <= -0.10:
                exit_price = buy_price * 0.90
                break
        if exit_price is None and not stock_data.empty:
            exit_price = float(stock_data["Close"].iloc[-1])
        if exit_price is None:
            month_end_value += allocation
            continue

        month_end_value += (units * exit_price) * (1 - TRANSACTION_COST)

    pnl = month_end_value - month_start
    return month_end_value, pnl


class JevFilter:
    def __init__(self, threshold: float = JEV_THRESHOLD, cache_path: Path = CACHE_PATH):
        self.threshold = threshold
        self.cache_path = cache_path
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache: dict[str, float] = {}
        if self.cache_path.exists():
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        load_secrets()
        require_env("TYPESAFE_API_KEY")
        from typesafe_sdk import Noul, TypeSafeClient

        self._Noul = Noul
        self._client = TypeSafeClient(model=JEV_MODEL)
        self.calls = 0
        self.cache_hits = 0

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
        self.cache_path.write_text(json.dumps(self.cache, indent=2, sort_keys=True), encoding="utf-8")

    def _state(self, name: str, ticker: str, prior_ret: float, data: pd.DataFrame, curr_month: pd.Timestamp) -> str:
        hist = data.loc[: curr_month - pd.Timedelta(days=1)].tail(21)
        closes = hist["Close"].astype(float)
        vol = float(closes.pct_change().std()) if len(closes) > 5 else 0.0
        last = float(closes.iloc[-1]) if len(closes) else 0.0
        return (
            f"NSE stock {name} ({ticker}). "
            f"Prior calendar-month return: {prior_ret*100:.2f}%. "
            f"Recent ~1m close volatility (std of daily returns): {vol*100:.2f}%. "
            f"Last close before entry month: {last:.2f}. "
            f"Strategy: equal-weight monthly momentum, hold about one month, hard -10% stop, "
            f"0.25% transaction cost each side. Indian cash equities."
        )

    def score(self, name: str, prior_ret: float, data: pd.DataFrame, curr_month: pd.Timestamp) -> float:
        key = f"{curr_month.strftime('%Y-%m')}|{name}|{prior_ret:.4f}"
        if key in self.cache:
            self.cache_hits += 1
            return float(self.cache[key])

        ticker = STOCKS_MAP[name]
        state = self._state(name, ticker, prior_ret, data, curr_month)
        response = self._client.system_one(
            state=state,
            questions={
                "approve_buy": self._Noul(
                    instructions=(
                        "For a short-term monthly momentum sleeve, should we BUY this name "
                        "for the upcoming month? Answer yes only if continuation odds look "
                        "reasonable versus crash/gap risk given the -10% stop."
                    )
                )
            },
        )
        noul = float(response.answers["approve_buy"].noul)
        self.cache[key] = noul
        self.calls += 1
        if self.calls % 10 == 0:
            self.cache_path.write_text(json.dumps(self.cache, indent=2, sort_keys=True), encoding="utf-8")
            print(f"  Jev calls so far: {self.calls} (cache hits {self.cache_hits})")
        return noul

    def filter_names(
        self,
        all_data: dict[str, pd.DataFrame],
        names: list[str],
        prev_month: pd.Timestamp,
        curr_month: pd.Timestamp,
    ) -> list[str]:
        kept = []
        for name in names:
            ret = prior_month_return(all_data[name], prev_month, curr_month)
            if ret is None:
                continue
            noul = self.score(name, ret, all_data[name], curr_month)
            mark = "PASS" if noul >= self.threshold else "SKIP"
            print(f"    Jev {curr_month.strftime('%Y-%m')} {name}: {noul:.2f} [{mark}]")
            if noul >= self.threshold:
                kept.append(name)
        return kept


def run_strategy(
    all_data: dict[str, pd.DataFrame],
    label: str,
    jev: JevFilter | None = None,
) -> dict:
    portfolio_value = INITIAL_CAPITAL
    annual_running_profit = 0.0
    months = pd.date_range(start=START_DATE, end=END_DATE, freq="MS")
    track_dates = [months[0]]
    track_values = [INITIAL_CAPITAL]
    months_traded = 0
    months_cash = 0
    picks_total = 0

    print(f"\n===== {label} =====")
    for i in range(1, len(months)):
        prev_month, curr_month = months[i - 1], months[i]
        investable = eligible_names(all_data, prev_month, curr_month)
        if jev is not None and investable:
            investable = jev.filter_names(all_data, investable, prev_month, curr_month)

        if investable:
            portfolio_value, pnl = simulate_month(all_data, investable, portfolio_value, curr_month)
            annual_running_profit += pnl
            monthly_return = (pnl / (portfolio_value - pnl)) * 100 if portfolio_value != pnl else 0.0
            # portfolio_value already new; use pnl / start
            start_cap = portfolio_value - pnl
            monthly_return = (pnl / start_cap) * 100 if start_cap else 0.0
            months_traded += 1
            picks_total += len(investable)
            print(
                f"{curr_month.strftime('%Y-%m'):<10} | {monthly_return:>8.2f}% | "
                f"{len(investable)} names | {', '.join(investable)}"
            )
        else:
            months_cash += 1

        if curr_month.month == 3:
            if annual_running_profit > 0:
                tax_amount = annual_running_profit * STCG_TAX_RATE
                portfolio_value -= tax_amount
                print(f"Tax paid in March {curr_month.year}: {tax_amount:.2f}")
            annual_running_profit = 0.0

        track_dates.append(curr_month)
        track_values.append(portfolio_value)

    if annual_running_profit > 0:
        tax_amount = annual_running_profit * STCG_TAX_RATE
        portfolio_value -= tax_amount
        print(f"Final tax adjustment: {tax_amount:.2f}")
        track_values[-1] = portfolio_value

    start_dt = pd.to_datetime(START_DATE)
    end_dt = pd.to_datetime(END_DATE)
    years = (end_dt - start_dt).days / 365.25
    total_return = ((portfolio_value - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    cagr = (((portfolio_value / INITIAL_CAPITAL) ** (1 / years)) - 1) * 100 if years > 0 else 0.0

    return {
        "label": label,
        "final_value": round(portfolio_value, 2),
        "total_return_pct": round(total_return, 2),
        "cagr_pct": round(cagr, 2),
        "months_traded": months_traded,
        "months_cash": months_cash,
        "avg_names_when_invested": round(picks_total / months_traded, 2) if months_traded else 0,
        "track_dates": [d.isoformat() for d in track_dates],
        "track_values": track_values,
    }


def buy_and_hold(all_data: dict[str, pd.DataFrame]) -> dict:
    valid = [n for n, d in all_data.items() if not d.empty]
    alloc = INITIAL_CAPITAL / len(valid)
    end_val = 0.0
    for name in valid:
        data = all_data[name]
        units = alloc / float(data["Open"].iloc[0])
        end_val += units * float(data["Close"].iloc[-1])
    years = (pd.to_datetime(END_DATE) - pd.to_datetime(START_DATE)).days / 365.25
    total_return = ((end_val - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    cagr = (((end_val / INITIAL_CAPITAL) ** (1 / years)) - 1) * 100
    return {
        "label": "Buy & Hold (equal weight universe)",
        "final_value": round(end_val, 2),
        "total_return_pct": round(total_return, 2),
        "cagr_pct": round(cagr, 2),
    }


def benchmark_return(ticker: str) -> float:
    df = yf.download(ticker, start=START_DATE, end=END_DATE, progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    return ((float(df[col].iloc[-1]) - float(df[col].iloc[0])) / float(df[col].iloc[0])) * 100


def main() -> None:
    os.makedirs(ROOT / "artifacts", exist_ok=True)
    print(f"Downloading universe ({len(STOCKS_MAP)} tickers) {START_DATE} -> {END_DATE} ...")
    all_data = download_universe(START_DATE, END_DATE)
    print(f"Loaded {len(all_data)} series with data.")

    baseline = run_strategy(all_data, "Baseline (no Jev)")

    jev = JevFilter(threshold=JEV_THRESHOLD)
    try:
        jev_run = run_strategy(all_data, f"Jev filter (noul>={JEV_THRESHOLD})", jev=jev)
    finally:
        jev.close()

    bah = buy_and_hold(all_data)
    nifty50 = round(benchmark_return("^NSEI"), 2)
    nifty500 = round(benchmark_return("^CRSLDX"), 2)

    summary = {
        "period": f"{START_DATE} to {END_DATE}",
        "initial_capital": INITIAL_CAPITAL,
        "jev_threshold": JEV_THRESHOLD,
        "jev_api_calls": jev.calls,
        "jev_cache_hits": jev.cache_hits,
        "baseline": {k: v for k, v in baseline.items() if k not in ("track_dates", "track_values")},
        "jev": {k: v for k, v in jev_run.items() if k not in ("track_dates", "track_values")},
        "buy_and_hold": bah,
        "nifty50_total_return_pct": nifty50,
        "nifty500_total_return_pct": nifty500,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Plot
    plt.figure(figsize=(12, 6))
    plt.plot(pd.to_datetime(baseline["track_dates"]), baseline["track_values"], label="Baseline")
    plt.plot(pd.to_datetime(jev_run["track_dates"]), jev_run["track_values"], label="Jev filter")
    plt.title("Monthly churn: Baseline vs Jev")
    plt.xlabel("Date")
    plt.ylabel("Portfolio value (INR)")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(PLOT_PATH, dpi=120)
    plt.close()

    print("\n" + "=" * 72)
    print("COMPARISON SUMMARY")
    print("=" * 72)
    rows = [
        {
            "Strategy": "Baseline",
            "Final": baseline["final_value"],
            "Return %": baseline["total_return_pct"],
            "CAGR %": baseline["cagr_pct"],
            "Months invested": baseline["months_traded"],
            "Cash months": baseline["months_cash"],
        },
        {
            "Strategy": "Jev filter",
            "Final": jev_run["final_value"],
            "Return %": jev_run["total_return_pct"],
            "CAGR %": jev_run["cagr_pct"],
            "Months invested": jev_run["months_traded"],
            "Cash months": jev_run["months_cash"],
        },
        {
            "Strategy": "Buy & Hold",
            "Final": bah["final_value"],
            "Return %": bah["total_return_pct"],
            "CAGR %": bah["cagr_pct"],
            "Months invested": "-",
            "Cash months": "-",
        },
    ]
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"\nNifty 50 total return:  {nifty50:.2f}%")
    print(f"Nifty 500 total return: {nifty500:.2f}%")
    print(f"Jev API calls: {jev.calls} | cache hits: {jev.cache_hits}")
    print(f"Saved: {SUMMARY_PATH}")
    print(f"Plot:  {PLOT_PATH}")
    print("=" * 72)


if __name__ == "__main__":
    main()
