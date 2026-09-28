#!/usr/bin/env python3
"""
Full Jev variant sweep on the monthly momentum strategy.

Core (all variants):
  - Prior calendar month return >= 10%
  - Buy at next month Open (equal weight unless variant says otherwise)
  - Per-stock -10% stop (fill buy*0.90) else month-end Close
  - 0.25% cost each side; 20% March STCG on running profit

Variants:
  1) no_jev_all              — all eligible equal weight
  2) top5_no_jev             — top 5 by momentum
  3) hard_filter_0.45/0.50/0.55
  4) soft_veto_0.35/0.40     — drop only clear nos
  5) weight_by_jev           — all eligible, weights ∝ noul
  6) hybrid_veto035_weight   — veto <0.35, weight rest by noul
  7) rerank_top10_top5
  8) rerank_top10_top3
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
TRANSACTION_COST = 0.0025
STCG_TAX_RATE = 0.20
INITIAL_CAPITAL = 100_000.0
STOP_PCT = 0.10
JEV_MODEL = "jev-latest"
START_DATE = "2021-09-01"
END_DATE = "2026-09-01"

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"
CACHE_PATH = ARTIFACTS / "jev_variant_sweep_cache.json"
LEGACY_CACHES = [
    ARTIFACTS / "jev_top5_5y_cache.json",
    ARTIFACTS / "jev_monthly_cache.json",
]
SUMMARY_PATH = ARTIFACTS / "jev_variant_sweep_comparison.json"
PLOT_PATH = ARTIFACTS / "jev_variant_sweep_comparison.png"

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
    path = ROOT / "bot_secrets.env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "TYPESAFE_API_KEY":
                val = v.strip().strip("'").strip('"')
                if val and "your-" not in val.lower() and "paste" not in val.lower():
                    os.environ["TYPESAFE_API_KEY"] = val
                    return val
    raise RuntimeError("TYPESAFE_API_KEY missing in bot_secrets.env")


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


def rank_eligible(all_data, prev_month, curr_month) -> list[tuple[str, float]]:
    ranked = []
    for name, data in all_data.items():
        ret = prior_month_return(data, prev_month, curr_month)
        if ret is not None and ret >= MIN_GROWTH:
            ranked.append((name, ret))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


class JevScorer:
    def __init__(self):
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        self.cache: dict[str, float] = {}
        for p in LEGACY_CACHES + [CACHE_PATH]:
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

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
        CACHE_PATH.write_text(json.dumps(self.cache, indent=2, sort_keys=True), encoding="utf-8")

    def score(self, name: str, prior_ret: float, data: pd.DataFrame, curr_month: pd.Timestamp) -> float:
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
            f"Strategy: monthly momentum, hold about one month, hard -10% stop, "
            f"0.25% transaction cost each side. Indian cash equities."
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
        if self.calls % 20 == 0:
            CACHE_PATH.write_text(json.dumps(self.cache, indent=2, sort_keys=True), encoding="utf-8")
            print(f"  Jev progress: calls={self.calls} hits={self.hits}")
        return noul


def simulate_month_weighted(
    all_data: dict[str, pd.DataFrame],
    weights: dict[str, float],
    portfolio_value: float,
    curr_month: pd.Timestamp,
) -> tuple[float, float]:
    if not weights:
        return portfolio_value, 0.0
    # normalize
    s = sum(weights.values())
    if s <= 0:
        return portfolio_value, 0.0
    weights = {k: v / s for k, v in weights.items()}
    start_cap = portfolio_value
    end_val = 0.0
    for stock, w in weights.items():
        allocation = portfolio_value * w
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


def max_drawdown(values: list[float]) -> float:
    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)
    return max_dd * 100


def select_weights(
    mode: str,
    ranked: list[tuple[str, float]],
    scores: dict[str, float],
) -> dict[str, float]:
    """Return name -> raw weight (will be normalized)."""
    if not ranked:
        return {}

    names_rets = ranked
    if mode == "no_jev_all":
        return {n: 1.0 for n, _ in names_rets}

    if mode == "top5_no_jev":
        return {n: 1.0 for n, _ in names_rets[:5]}

    if mode.startswith("hard_filter_"):
        thr = float(mode.split("_")[-1])
        kept = [n for n, _ in names_rets if scores.get(n, 0) >= thr]
        return {n: 1.0 for n in kept}

    if mode.startswith("soft_veto_"):
        thr = float(mode.split("_")[-1])
        kept = [n for n, _ in names_rets if scores.get(n, 0) >= thr]
        return {n: 1.0 for n in kept}

    if mode == "weight_by_jev":
        return {n: max(scores.get(n, 0.01), 0.01) for n, _ in names_rets}

    if mode == "hybrid_veto035_weight":
        kept = [(n, scores.get(n, 0)) for n, _ in names_rets if scores.get(n, 0) >= 0.35]
        return {n: max(s, 0.01) for n, s in kept}

    if mode == "rerank_top10_top5":
        pool = names_rets[:10]
        ordered = sorted(pool, key=lambda x: scores.get(x[0], 0), reverse=True)[:5]
        return {n: 1.0 for n, _ in ordered}

    if mode == "rerank_top10_top3":
        pool = names_rets[:10]
        ordered = sorted(pool, key=lambda x: scores.get(x[0], 0), reverse=True)[:3]
        return {n: 1.0 for n, _ in ordered}

    raise ValueError(mode)


def run_variant(
    all_data: dict[str, pd.DataFrame],
    monthly_ranked: dict[pd.Timestamp, list[tuple[str, float]]],
    monthly_scores: dict[pd.Timestamp, dict[str, float]],
    mode: str,
) -> dict:
    portfolio_value = INITIAL_CAPITAL
    annual_pnl = 0.0
    months = sorted(monthly_ranked.keys())
    # months keys are buy months (curr_month)
    track_dates = [pd.Timestamp(START_DATE)]
    track_values = [INITIAL_CAPITAL]
    months_traded = 0
    months_cash = 0
    picks_total = 0

    for curr_month in months:
        ranked = monthly_ranked[curr_month]
        scores = monthly_scores.get(curr_month, {})
        weights = select_weights(mode, ranked, scores)
        if weights:
            portfolio_value, pnl = simulate_month_weighted(all_data, weights, portfolio_value, curr_month)
            annual_pnl += pnl
            months_traded += 1
            picks_total += len(weights)
        else:
            months_cash += 1

        if curr_month.month == 3:
            if annual_pnl > 0:
                portfolio_value -= annual_pnl * STCG_TAX_RATE
            annual_pnl = 0.0

        track_dates.append(curr_month)
        track_values.append(portfolio_value)

    if annual_pnl > 0:
        portfolio_value -= annual_pnl * STCG_TAX_RATE
        track_values[-1] = portfolio_value

    years = (pd.to_datetime(END_DATE) - pd.to_datetime(START_DATE)).days / 365.25
    total_return = ((portfolio_value - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    cagr = (((max(portfolio_value, 1e-9) / INITIAL_CAPITAL) ** (1 / years)) - 1) * 100
    return {
        "mode": mode,
        "final_value": round(portfolio_value, 2),
        "total_return_pct": round(total_return, 2),
        "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": round(max_drawdown(track_values), 2),
        "months_traded": months_traded,
        "months_cash": months_cash,
        "avg_names": round(picks_total / months_traded, 2) if months_traded else 0,
        "track_dates": [d.isoformat() for d in track_dates],
        "track_values": track_values,
    }


def buy_and_hold(all_data: dict[str, pd.DataFrame]) -> dict:
    valid = [n for n, d in all_data.items() if not d.empty]
    alloc = INITIAL_CAPITAL / len(valid)
    end_val = 0.0
    for name in valid:
        window = all_data[name].loc[START_DATE:END_DATE]
        if window.empty:
            continue
        units = alloc / float(window["Open"].iloc[0])
        end_val += units * float(window["Close"].iloc[-1])
    years = (pd.to_datetime(END_DATE) - pd.to_datetime(START_DATE)).days / 365.25
    ret = ((end_val - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    cagr = (((end_val / INITIAL_CAPITAL) ** (1 / years)) - 1) * 100
    return {
        "mode": "buy_and_hold",
        "final_value": round(end_val, 2),
        "total_return_pct": round(ret, 2),
        "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": None,
        "months_traded": None,
        "months_cash": None,
        "avg_names": None,
    }


def benchmark_return(ticker: str) -> float:
    df = yf.download(ticker, start=START_DATE, end=END_DATE, progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    return ((float(df[col].iloc[-1]) - float(df[col].iloc[0])) / float(df[col].iloc[0])) * 100


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    print(f"Period {START_DATE} -> {END_DATE}")
    print("Downloading ...")
    all_data = download_universe(START_DATE, END_DATE)
    print(f"Loaded {len(all_data)} series")

    months = pd.date_range(start=START_DATE, end=END_DATE, freq="MS")
    monthly_ranked: dict[pd.Timestamp, list[tuple[str, float]]] = {}
    for i in range(1, len(months)):
        prev_month, curr_month = months[i - 1], months[i]
        monthly_ranked[curr_month] = rank_eligible(all_data, prev_month, curr_month)

    # Score every eligible name once (cache-backed)
    jev = JevScorer()
    monthly_scores: dict[pd.Timestamp, dict[str, float]] = {}
    try:
        total_needed = sum(len(v) for v in monthly_ranked.values())
        print(f"Scoring {total_needed} eligible name-months with Jev (cache size {len(jev.cache)}) ...")
        done = 0
        for curr_month, ranked in monthly_ranked.items():
            scores = {}
            for name, ret in ranked:
                scores[name] = jev.score(name, ret, all_data[name], curr_month)
                done += 1
            monthly_scores[curr_month] = scores
        print(f"Scoring done. API calls={jev.calls} cache_hits={jev.hits}")
    finally:
        jev.close()

    modes = [
        "no_jev_all",
        "top5_no_jev",
        "hard_filter_0.45",
        "hard_filter_0.50",
        "hard_filter_0.55",
        "soft_veto_0.35",
        "soft_veto_0.40",
        "weight_by_jev",
        "hybrid_veto035_weight",
        "rerank_top10_top5",
        "rerank_top10_top3",
    ]

    results = []
    for mode in modes:
        print(f"Running {mode} ...")
        results.append(run_variant(all_data, monthly_ranked, monthly_scores, mode))

    bah = buy_and_hold(all_data)
    n50 = round(benchmark_return("^NSEI"), 2)
    n500 = round(benchmark_return("^CRSLDX"), 2)

    def slim(r: dict) -> dict:
        return {k: v for k, v in r.items() if k not in ("track_dates", "track_values")}

    ranked = sorted(results, key=lambda r: r["cagr_pct"], reverse=True)
    summary = {
        "period": f"{START_DATE} to {END_DATE}",
        "initial_capital": INITIAL_CAPITAL,
        "jev_api_calls": jev.calls,
        "jev_cache_hits": jev.hits,
        "nifty50_total_return_pct": n50,
        "nifty500_total_return_pct": n500,
        "buy_and_hold": slim(bah),
        "variants_ranked_by_cagr": [slim(r) for r in ranked],
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Plot top variants + no_jev + bah equity of strategy variants
    plt.figure(figsize=(12, 6))
    highlight = {"no_jev_all", "weight_by_jev", "rerank_top10_top5", "soft_veto_0.35", "hard_filter_0.50"}
    for r in results:
        if r["mode"] in highlight or r["mode"] == ranked[0]["mode"]:
            plt.plot(pd.to_datetime(r["track_dates"]), r["track_values"], label=r["mode"])
    plt.title("Jev variant sweep — monthly momentum")
    plt.xlabel("Date")
    plt.ylabel("Portfolio value (INR)")
    plt.grid(True)
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(PLOT_PATH, dpi=120)
    plt.close()

    print("\n" + "=" * 88)
    print("JEV VARIANT SWEEP — RANKED BY CAGR")
    print("=" * 88)
    df = pd.DataFrame([slim(r) for r in ranked])
    print(df.to_string(index=False))
    print(f"\nBuy & Hold: {bah['total_return_pct']}% | CAGR {bah['cagr_pct']}%")
    print(f"Nifty 50: {n50}% | Nifty 500: {n500}%")
    print(f"Winner: {ranked[0]['mode']} | CAGR {ranked[0]['cagr_pct']}% | Final {ranked[0]['final_value']}")
    print(f"Saved: {SUMMARY_PATH}")
    print("=" * 88)


if __name__ == "__main__":
    main()
