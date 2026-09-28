#!/usr/bin/env python3
"""
5-year monthly Top-5 momentum backtest: without Jev vs with Jev.

Rules (shared):
  - Rank universe by prior calendar-month return
  - Require prior-month return >= 10% (same as original strategy)
  - Take Top 5 by that return (or fewer if <5 eligible)
  - Equal-weight buy at next month Open
  - Exit on -10% stop (fill at buy*0.90) or month-end Close
  - 0.25% cost each side; 20% STCG drag each March on running profit

Variants:
  A) Top5 — no Jev
  B) Top5 candidates, keep only Jev approve_buy >= threshold (may hold <5 / cash)
  C) Top10 by momentum, re-rank by Jev noul, take Top 5 (always up to 5)

Period: last ~5 years (month-aligned).
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

warnings.simplefilter(action="ignore", category=FutureWarning)

MIN_GROWTH = 0.10
TOP_N = 5
JEV_CANDIDATE_POOL = 10  # for re-rank variant
TRANSACTION_COST = 0.0025
STCG_TAX_RATE = 0.20
INITIAL_CAPITAL = 100_000.0
STOP_PCT = 0.10
JEV_THRESHOLD = 0.50
JEV_MODEL = "jev-latest"

# Last 5 years, month-aligned (as of ~2026-09)
START_DATE = "2021-09-01"
END_DATE = "2026-09-01"

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"
CACHE_PATH = ARTIFACTS / "jev_top5_5y_cache.json"
SUMMARY_PATH = ARTIFACTS / "top5_monthly_5y_comparison.json"
PLOT_PATH = ARTIFACTS / "top5_monthly_5y_comparison.png"

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


def load_typesafe_key() -> str:
    env_path = ROOT / "bot_secrets.env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "TYPESAFE_API_KEY":
                val = v.strip().strip("'").strip('"')
                if val and "your-" not in val.lower() and "paste" not in val.lower():
                    os.environ["TYPESAFE_API_KEY"] = val
                    return val
    val = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not val:
        raise RuntimeError("TYPESAFE_API_KEY missing in bot_secrets.env")
    return val


def download_universe(start: str, end: str) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for name, ticker in STOCKS_MAP.items():
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
        if df is None or df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        out[name] = df
    return out


def prior_month_return(data: pd.DataFrame, prev_month: pd.Timestamp, curr_month: pd.Timestamp) -> float | None:
    period = data.loc[prev_month : curr_month - pd.Timedelta(days=1)]
    if len(period) <= 1:
        return None
    a = float(period["Close"].iloc[0])
    b = float(period["Close"].iloc[-1])
    if a <= 0:
        return None
    return (b - a) / a


def rank_names(
    all_data: dict[str, pd.DataFrame],
    prev_month: pd.Timestamp,
    curr_month: pd.Timestamp,
    min_growth: float | None = MIN_GROWTH,
) -> list[tuple[str, float]]:
    ranked: list[tuple[str, float]] = []
    for name, data in all_data.items():
        ret = prior_month_return(data, prev_month, curr_month)
        if ret is None:
            continue
        if min_growth is not None and ret < min_growth:
            continue
        ranked.append((name, ret))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


def simulate_month(
    all_data: dict[str, pd.DataFrame],
    names: list[str],
    portfolio_value: float,
    curr_month: pd.Timestamp,
) -> tuple[float, float]:
    if not names:
        return portfolio_value, 0.0
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
    return end_val, end_val - start_cap


class JevScorer:
    def __init__(self, cache_path: Path = CACHE_PATH):
        self.cache_path = cache_path
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache: dict[str, float] = {}
        if self.cache_path.exists():
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        # also merge older monthly cache if present
        legacy = ARTIFACTS / "jev_monthly_cache.json"
        if legacy.exists():
            try:
                self.cache.update(json.loads(legacy.read_text(encoding="utf-8")))
            except Exception:
                pass
        load_typesafe_key()
        from typesafe_sdk import Noul, TypeSafeClient

        self._Noul = Noul
        self._client = TypeSafeClient(model=JEV_MODEL)
        self.calls = 0
        self.hits = 0

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
        self.cache_path.write_text(json.dumps(self.cache, indent=2, sort_keys=True), encoding="utf-8")

    def score(
        self,
        name: str,
        prior_ret: float,
        data: pd.DataFrame,
        curr_month: pd.Timestamp,
    ) -> float:
        key = f"{curr_month.strftime('%Y-%m')}|{name}|{prior_ret:.4f}"
        if key in self.cache:
            self.hits += 1
            return float(self.cache[key])
        hist = data.loc[: curr_month - pd.Timedelta(days=1)].tail(21)
        closes = hist["Close"].astype(float)
        vol = float(closes.pct_change().std()) if len(closes) > 5 else 0.0
        last = float(closes.iloc[-1]) if len(closes) else 0.0
        state = (
            f"NSE stock {name} ({STOCKS_MAP[name]}). "
            f"Prior calendar-month return: {prior_ret*100:.2f}%. "
            f"Recent ~1m close volatility (std of daily returns): {vol*100:.2f}%. "
            f"Last close before entry month: {last:.2f}. "
            f"Strategy: equal-weight monthly momentum top-{TOP_N}, hold about one month, "
            f"hard -10% stop, 0.25% transaction cost each side. Indian cash equities."
        )
        resp = self._client.system_one(
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
        noul = float(resp.answers["approve_buy"].noul)
        self.cache[key] = noul
        self.calls += 1
        if self.calls % 15 == 0:
            self.cache_path.write_text(json.dumps(self.cache, indent=2, sort_keys=True), encoding="utf-8")
            print(f"  Jev calls={self.calls} cache_hits={self.hits}")
        return noul


def summarize(label: str, final_value: float, track_dates, track_values, extra: dict | None = None) -> dict:
    years = (pd.to_datetime(END_DATE) - pd.to_datetime(START_DATE)).days / 365.25
    total_return = ((final_value - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    cagr = (((max(final_value, 1e-9) / INITIAL_CAPITAL) ** (1 / years)) - 1) * 100
    out = {
        "label": label,
        "final_value": round(final_value, 2),
        "total_return_pct": round(total_return, 2),
        "cagr_pct": round(cagr, 2),
        "track_dates": [pd.Timestamp(d).isoformat() for d in track_dates],
        "track_values": [float(v) for v in track_values],
    }
    if extra:
        out.update(extra)
    return out


def run_variant(
    all_data: dict[str, pd.DataFrame],
    label: str,
    mode: str,
    jev: JevScorer | None = None,
    jev_threshold: float = JEV_THRESHOLD,
) -> dict:
    """
    mode:
      - top5: top 5 by momentum (>=10%)
      - jev_filter: top 5 by momentum, keep noul >= threshold
      - jev_rerank: top 10 by momentum (>=10% if enough), score, take top 5 by noul
    """
    portfolio_value = INITIAL_CAPITAL
    annual_pnl = 0.0
    months = pd.date_range(start=START_DATE, end=END_DATE, freq="MS")
    track_dates = [months[0]]
    track_values = [INITIAL_CAPITAL]
    months_traded = 0
    months_cash = 0
    picks_total = 0

    print(f"\n===== {label} =====")
    for i in range(1, len(months)):
        prev_month, curr_month = months[i - 1], months[i]
        ranked = rank_names(all_data, prev_month, curr_month, min_growth=MIN_GROWTH)

        if mode == "top5":
            names = [n for n, _ in ranked[:TOP_N]]
        elif mode == "jev_filter":
            assert jev is not None
            cand = ranked[:TOP_N]
            names = []
            for n, ret in cand:
                noul = jev.score(n, ret, all_data[n], curr_month)
                mark = "PASS" if noul >= jev_threshold else "SKIP"
                print(f"    {curr_month.strftime('%Y-%m')} {n}: mom={ret*100:.1f}% jev={noul:.2f} [{mark}]")
                if noul >= jev_threshold:
                    names.append(n)
        elif mode == "jev_rerank":
            assert jev is not None
            pool = ranked[:JEV_CANDIDATE_POOL]
            scored = []
            for n, ret in pool:
                noul = jev.score(n, ret, all_data[n], curr_month)
                scored.append((n, ret, noul))
            scored.sort(key=lambda x: x[2], reverse=True)
            names = [n for n, _, _ in scored[:TOP_N]]
            if scored:
                detail = ", ".join(f"{n}:{j:.2f}" for n, _, j in scored[:TOP_N])
                print(f"    {curr_month.strftime('%Y-%m')} top5-by-jev: {detail}")
        else:
            raise ValueError(mode)

        if names:
            portfolio_value, pnl = simulate_month(all_data, names, portfolio_value, curr_month)
            annual_pnl += pnl
            start_cap = portfolio_value - pnl
            mret = (pnl / start_cap) * 100 if start_cap else 0.0
            months_traded += 1
            picks_total += len(names)
            print(f"{curr_month.strftime('%Y-%m'):<10} | {mret:>7.2f}% | {len(names)} | {', '.join(names)}")
        else:
            months_cash += 1

        if curr_month.month == 3:
            if annual_pnl > 0:
                tax = annual_pnl * STCG_TAX_RATE
                portfolio_value -= tax
                print(f"Tax March {curr_month.year}: {tax:.2f}")
            annual_pnl = 0.0

        track_dates.append(curr_month)
        track_values.append(portfolio_value)

    if annual_pnl > 0:
        portfolio_value -= annual_pnl * STCG_TAX_RATE
        track_values[-1] = portfolio_value

    return summarize(
        label,
        portfolio_value,
        track_dates,
        track_values,
        {
            "months_traded": months_traded,
            "months_cash": months_cash,
            "avg_names": round(picks_total / months_traded, 2) if months_traded else 0,
        },
    )


def buy_and_hold(all_data: dict[str, pd.DataFrame]) -> dict:
    valid = [n for n, d in all_data.items() if not d.empty]
    # align to first date >= START
    end_val = 0.0
    alloc = INITIAL_CAPITAL / len(valid)
    for name in valid:
        data = all_data[name]
        window = data.loc[START_DATE:END_DATE]
        if window.empty:
            continue
        units = alloc / float(window["Open"].iloc[0])
        end_val += units * float(window["Close"].iloc[-1])
    return summarize("Buy & Hold (equal weight)", end_val, [START_DATE, END_DATE], [INITIAL_CAPITAL, end_val])


def benchmark_return(ticker: str) -> float:
    df = yf.download(ticker, start=START_DATE, end=END_DATE, progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    return ((float(df[col].iloc[-1]) - float(df[col].iloc[0])) / float(df[col].iloc[0])) * 100


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    print(f"Period {START_DATE} -> {END_DATE} | Top {TOP_N} | min growth {MIN_GROWTH*100:.0f}%")
    print("Downloading universe ...")
    all_data = download_universe(START_DATE, END_DATE)
    print(f"Loaded {len(all_data)} series")

    top5 = run_variant(all_data, "Top5 momentum (no Jev)", mode="top5")

    jev = JevScorer()
    try:
        jev_filter = run_variant(
            all_data,
            f"Top5 then Jev filter (noul>={JEV_THRESHOLD})",
            mode="jev_filter",
            jev=jev,
            jev_threshold=JEV_THRESHOLD,
        )
        jev_rerank = run_variant(
            all_data,
            f"Top10 mom → Top5 by Jev",
            mode="jev_rerank",
            jev=jev,
        )
    finally:
        jev.close()

    bah = buy_and_hold(all_data)
    n50 = round(benchmark_return("^NSEI"), 2)
    n500 = round(benchmark_return("^CRSLDX"), 2)

    def slim(d: dict) -> dict:
        return {k: v for k, v in d.items() if k not in ("track_dates", "track_values")}

    summary = {
        "period": f"{START_DATE} to {END_DATE}",
        "initial_capital": INITIAL_CAPITAL,
        "rules": {
            "min_prior_month_return": MIN_GROWTH,
            "top_n": TOP_N,
            "stop": STOP_PCT,
            "cost_each_side": TRANSACTION_COST,
            "jev_threshold_filter": JEV_THRESHOLD,
            "jev_model": JEV_MODEL,
        },
        "top5_no_jev": slim(top5),
        "top5_jev_filter": slim(jev_filter),
        "top5_jev_rerank": slim(jev_rerank),
        "buy_and_hold": slim(bah),
        "nifty50_total_return_pct": n50,
        "nifty500_total_return_pct": n500,
        "jev_api_calls": jev.calls,
        "jev_cache_hits": jev.hits,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    plt.figure(figsize=(12, 6))
    for run in (top5, jev_filter, jev_rerank):
        plt.plot(pd.to_datetime(run["track_dates"]), run["track_values"], label=run["label"])
    plt.title("Top-5 monthly momentum: with vs without Jev (5y)")
    plt.xlabel("Date")
    plt.ylabel("Portfolio value (INR)")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(PLOT_PATH, dpi=120)
    plt.close()

    rows = [slim(top5), slim(jev_filter), slim(jev_rerank), slim(bah)]
    print("\n" + "=" * 72)
    print("5-YEAR TOP-5 COMPARISON")
    print("=" * 72)
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"\nNifty 50: {n50}% | Nifty 500: {n500}%")
    print(f"Jev API calls: {jev.calls} | cache hits: {jev.hits}")
    print(f"Saved: {SUMMARY_PATH}")
    print(f"Plot:  {PLOT_PATH}")
    print("=" * 72)


if __name__ == "__main__":
    main()
