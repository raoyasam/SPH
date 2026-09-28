#!/usr/bin/env python3
"""
Monthly momentum strategy on current Nifty 500 universe (Yahoo .NS).

Variants:
  - no_jev_all: all prior-month >=10%, equal weight
  - top5_no_jev: top 5 by prior-month return among >=10%
  - rerank_top10_top5: top 10 by mom, re-rank by Jev, take top 5

Same hold/SL/cost/tax rules as curated-list tests.
NOTE: Using today's Nifty 500 membership → survivorship bias vs true historical index.
"""

from __future__ import annotations

import json
import os
import time
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf

warnings.simplefilter(action="ignore", category=FutureWarning)

MIN_GROWTH = 0.10
TRANSACTION_COST = 0.0025
STCG_TAX_RATE = 0.20
INITIAL_CAPITAL = 100_000.0
STOP_PCT = 0.10
START_DATE = "2021-09-01"
END_DATE = "2026-09-01"
JEV_MODEL = "jev-latest"

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"
NIFTY_CSV = ARTIFACTS / "ind_nifty500list.csv"
CACHE_PATH = ARTIFACTS / "jev_nifty500_cache.json"
SUMMARY_PATH = ARTIFACTS / "nifty500_monthly_comparison.json"
PLOT_PATH = ARTIFACTS / "nifty500_monthly_comparison.png"
PRICES_CACHE = ARTIFACTS / "nifty500_prices_panel.parquet"


def load_typesafe_key() -> str:
    path = ROOT / "bot_secrets.env"
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("TYPESAFE_API_KEY"):
            val = line.split("=", 1)[1].strip().strip("'\"")
            if val and "paste" not in val.lower() and "your-" not in val.lower():
                os.environ["TYPESAFE_API_KEY"] = val
                return val
    raise RuntimeError("TYPESAFE_API_KEY missing")


def load_nifty500_tickers() -> list[str]:
    if not NIFTY_CSV.exists():
        raise FileNotFoundError(NIFTY_CSV)
    df = pd.read_csv(NIFTY_CSV)
    syms = df["Symbol"].astype(str).str.strip().tolist()
    # Yahoo NSE suffix
    return [f"{s}.NS" for s in syms if s and s.lower() != "nan"]


def download_panel(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """Download in batches; return {yahoo_ticker: OHLCV}."""
    out: dict[str, pd.DataFrame] = {}
    batch_size = 50
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        print(f"  download batch {i//batch_size+1}/{(len(tickers)-1)//batch_size+1} ({len(batch)} tickers)")
        data = yf.download(
            batch,
            start=start,
            end=end,
            progress=False,
            auto_adjust=False,
            group_by="ticker",
            threads=True,
        )
        time.sleep(0.5)
        if data is None or data.empty:
            continue
        # multi-ticker: columns MultiIndex (ticker, field)
        if isinstance(data.columns, pd.MultiIndex):
            level0 = data.columns.get_level_values(0).unique()
            for t in batch:
                if t not in level0:
                    continue
                sub = data[t].dropna(how="all")
                if sub.empty or "Close" not in sub.columns:
                    continue
                out[t] = sub
        else:
            # single ticker edge case
            if len(batch) == 1:
                out[batch[0]] = data.dropna(how="all")
    return out


def prior_month_return(df: pd.DataFrame, prev_month: pd.Timestamp, curr_month: pd.Timestamp) -> float | None:
    period = df.loc[prev_month : curr_month - pd.Timedelta(days=1)]
    if len(period) <= 1:
        return None
    a = float(period["Close"].iloc[0])
    b = float(period["Close"].iloc[-1])
    if a <= 0:
        return None
    return (b - a) / a


def rank_eligible(all_data: dict[str, pd.DataFrame], prev_month, curr_month) -> list[tuple[str, float]]:
    ranked = []
    for t, df in all_data.items():
        ret = prior_month_return(df, prev_month, curr_month)
        if ret is not None and ret >= MIN_GROWTH:
            ranked.append((t, ret))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


def simulate_month(all_data, names: list[str], portfolio_value: float, curr_month: pd.Timestamp) -> tuple[float, float]:
    if not names:
        return portfolio_value, 0.0
    allocation = portfolio_value / len(names)
    start = portfolio_value
    end_val = 0.0
    for t in names:
        df = all_data[t]
        buy = df.loc[curr_month : curr_month + pd.Timedelta(days=5)]
        if buy.empty:
            end_val += allocation
            continue
        buy_px = float(buy["Open"].iloc[0])
        units = (allocation * (1 - TRANSACTION_COST)) / buy_px
        hold = df.loc[curr_month : curr_month + pd.Timedelta(days=31)]
        exit_px = None
        for _, row in hold.iterrows():
            if (float(row["Close"]) - buy_px) / buy_px <= -STOP_PCT:
                exit_px = buy_px * (1 - STOP_PCT)
                break
        if exit_px is None and not hold.empty:
            exit_px = float(hold["Close"].iloc[-1])
        if exit_px is None:
            end_val += allocation
            continue
        end_val += units * exit_px * (1 - TRANSACTION_COST)
    return end_val, end_val - start


def max_drawdown(values: list[float]) -> float:
    peak = values[0]
    dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            dd = max(dd, (peak - v) / peak)
    return dd * 100


class JevScorer:
    def __init__(self):
        self.cache = {}
        if CACHE_PATH.exists() and CACHE_PATH.stat().st_size > 2:
            self.cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        # reuse prior caches where keys match
        for p in [ARTIFACTS / "jev_variant_sweep_cache.json", ARTIFACTS / "jev_top5_5y_cache.json"]:
            if p.exists() and p.stat().st_size > 2:
                try:
                    self.cache.update(json.loads(p.read_text(encoding="utf-8")))
                except Exception:
                    pass
        load_typesafe_key()
        from typesafe_sdk import Noul, TypeSafeClient

        self._Noul = Noul
        self._client = TypeSafeClient(model=JEV_MODEL)
        self.calls = 0
        self.hits = 0

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass
        CACHE_PATH.write_text(json.dumps(self.cache, indent=2, sort_keys=True), encoding="utf-8")

    def score(self, ticker: str, prior_ret: float, df: pd.DataFrame, curr_month: pd.Timestamp) -> float:
        key = f"{curr_month.strftime('%Y-%m')}|{ticker}|{prior_ret:.4f}"
        if key in self.cache:
            self.hits += 1
            return float(self.cache[key])
        hist = df.loc[: curr_month - pd.Timedelta(days=1)].tail(21)
        closes = hist["Close"].astype(float)
        vol = float(closes.pct_change().std()) if len(closes) > 5 else 0.0
        last = float(closes.iloc[-1]) if len(closes) else 0.0
        state = (
            f"NSE stock {ticker}. Prior calendar-month return: {prior_ret*100:.2f}%. "
            f"Recent ~1m close volatility: {vol*100:.2f}%. Last close: {last:.2f}. "
            f"Strategy: monthly momentum on Nifty 500 universe, hold ~1 month, -10% stop, "
            f"0.25% costs. Indian cash equities."
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
        if self.calls % 25 == 0:
            CACHE_PATH.write_text(json.dumps(self.cache, indent=2, sort_keys=True), encoding="utf-8")
            print(f"  Jev calls={self.calls} hits={self.hits}")
        return noul


def run_variant(all_data, mode: str, jev: JevScorer | None = None) -> dict:
    portfolio = INITIAL_CAPITAL
    annual_pnl = 0.0
    months = pd.date_range(START_DATE, END_DATE, freq="MS")
    track_d = [months[0]]
    track_v = [INITIAL_CAPITAL]
    traded = cash = picks = 0

    print(f"\n===== {mode} =====")
    for i in range(1, len(months)):
        prev_m, curr_m = months[i - 1], months[i]
        ranked = rank_eligible(all_data, prev_m, curr_m)

        if mode == "no_jev_all":
            names = [t for t, _ in ranked]
        elif mode == "top5_no_jev":
            names = [t for t, _ in ranked[:5]]
        elif mode == "rerank_top10_top5":
            assert jev is not None
            pool = ranked[:10]
            scored = [(t, ret, jev.score(t, ret, all_data[t], curr_m)) for t, ret in pool]
            scored.sort(key=lambda x: x[2], reverse=True)
            names = [t for t, _, _ in scored[:5]]
        else:
            raise ValueError(mode)

        if names:
            portfolio, pnl = simulate_month(all_data, names, portfolio, curr_m)
            annual_pnl += pnl
            traded += 1
            picks += len(names)
            if i <= 3 or i % 12 == 0:
                print(f"{curr_m.strftime('%Y-%m')} | n={len(names):3d} | eligible={len(ranked):3d}")
        else:
            cash += 1

        if curr_m.month == 3:
            if annual_pnl > 0:
                portfolio -= annual_pnl * STCG_TAX_RATE
            annual_pnl = 0.0
        track_d.append(curr_m)
        track_v.append(portfolio)

    if annual_pnl > 0:
        portfolio -= annual_pnl * STCG_TAX_RATE
        track_v[-1] = portfolio

    years = (pd.to_datetime(END_DATE) - pd.to_datetime(START_DATE)).days / 365.25
    total_ret = ((portfolio - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    cagr = (((max(portfolio, 1e-9) / INITIAL_CAPITAL) ** (1 / years)) - 1) * 100
    return {
        "mode": mode,
        "final_value": round(portfolio, 2),
        "total_return_pct": round(total_ret, 2),
        "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": round(max_drawdown(track_v), 2),
        "months_traded": traded,
        "months_cash": cash,
        "avg_names": round(picks / traded, 2) if traded else 0,
        "track_dates": [d.isoformat() for d in track_d],
        "track_values": track_v,
    }


def benchmark_return(ticker: str) -> float:
    df = yf.download(ticker, start=START_DATE, end=END_DATE, progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    return ((float(df[col].iloc[-1]) - float(df[col].iloc[0])) / float(df[col].iloc[0])) * 100


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    tickers = load_nifty500_tickers()
    print(f"Nifty 500 tickers: {len(tickers)}")
    print(f"Period {START_DATE} -> {END_DATE}")
    print("Downloading Yahoo prices (batched) ...")
    all_data = download_panel(tickers, START_DATE, END_DATE)
    print(f"Loaded price series: {len(all_data)} / {len(tickers)}")

    # symbol-only map for display
    results = []
    r0 = run_variant(all_data, "no_jev_all")
    results.append(r0)
    r1 = run_variant(all_data, "top5_no_jev")
    results.append(r1)

    jev = JevScorer()
    try:
        r2 = run_variant(all_data, "rerank_top10_top5", jev=jev)
        results.append(r2)
    finally:
        jev.close()

    n500 = round(benchmark_return("^CRSLDX"), 2)
    n50 = round(benchmark_return("^NSEI"), 2)

    def slim(r):
        return {k: v for k, v in r.items() if k not in ("track_dates", "track_values")}

    ranked = sorted(results, key=lambda x: x["cagr_pct"], reverse=True)
    summary = {
        "universe": "Nifty 500 (current membership as of list download)",
        "survivorship_bias_note": True,
        "period": f"{START_DATE} to {END_DATE}",
        "tickers_listed": len(tickers),
        "tickers_with_data": len(all_data),
        "initial_capital": INITIAL_CAPITAL,
        "jev_api_calls": jev.calls,
        "jev_cache_hits": jev.hits,
        "nifty50_total_return_pct": n50,
        "nifty500_total_return_pct": n500,
        "variants_ranked_by_cagr": [slim(r) for r in ranked],
        "compare_to_curated_list_5y": {
            "note": "From prior sweep on hand-picked ~35 names, same period",
            "rerank_top10_top5_cagr_pct": 25.0,
            "no_jev_all_cagr_pct": 21.95,
            "top5_no_jev_cagr_pct": 18.14,
        },
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    plt.figure(figsize=(12, 6))
    for r in results:
        plt.plot(pd.to_datetime(r["track_dates"]), r["track_values"], label=r["mode"])
    plt.title("Nifty 500 universe — monthly momentum +/- Jev")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(PLOT_PATH, dpi=120)
    plt.close()

    print("\n" + "=" * 72)
    print("NIFTY 500 UNIVERSE RESULTS")
    print("=" * 72)
    print(pd.DataFrame([slim(r) for r in ranked]).to_string(index=False))
    print(f"\nNifty 50: {n50}% | Nifty 500 index: {n500}%")
    print(f"Jev calls={jev.calls} hits={jev.hits}")
    print(f"Saved {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
