#!/usr/bin/env python3
"""
Live monthly momentum algo + Jev approve_buy scores.

1) Pull Yahoo OHLC for the universe
2) For each of the last two completed calendar months, list names with return >= 10%
3) Score the latest month's eligible list with TypeSafe Jev (Noul approve_buy)
"""

from __future__ import annotations

import json
import os
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.simplefilter(action="ignore", category=FutureWarning)

MIN_GROWTH = 0.10
JEV_THRESHOLDS = (0.45, 0.50, 0.55)
JEV_MODEL = "jev-latest"
ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"
OUT_JSON = ARTIFACTS / "live_monthly_jev_scores.json"

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


def load_typesafe_key() -> str | None:
    env_path = ROOT / "bot_secrets.env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "TYPESAFE_API_KEY":
                val = v.strip().strip("'").strip('"')
                if val and "your-" not in val.lower():
                    os.environ["TYPESAFE_API_KEY"] = val
                    return val
    val = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if val and "your-" not in val.lower():
        return val
    return None


def month_starts_back(n_complete: int = 2) -> list[tuple[pd.Timestamp, pd.Timestamp, str]]:
    """Return list of (month_start, next_month_start, label) for last n completed months."""
    today = pd.Timestamp.now(tz=None).normalize()
    # current month start
    cur = pd.Timestamp(year=today.year, month=today.month, day=1)
    out = []
    m = cur
    for _ in range(n_complete):
        m = m - pd.offsets.MonthBegin(1)
        nxt = m + pd.offsets.MonthBegin(1)
        out.append((m, nxt, m.strftime("%Y-%m")))
    out.reverse()  # chronological
    return out


def download_all(start: str, end: str) -> dict[str, pd.DataFrame]:
    data: dict[str, pd.DataFrame] = {}
    for name, ticker in STOCKS_MAP.items():
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
        if df is None or df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        data[name] = df
    return data


def month_return(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    period = df.loc[start : end - pd.Timedelta(days=1)]
    if len(period) <= 1:
        return None
    a = float(period["Close"].iloc[0])
    b = float(period["Close"].iloc[-1])
    if a <= 0:
        return None
    return (b - a) / a


def eligible_for_month(all_data: dict[str, pd.DataFrame], start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    rows = []
    for name, df in all_data.items():
        ret = month_return(df, start, end)
        if ret is None:
            continue
        rows.append(
            {
                "name": name,
                "ticker": STOCKS_MAP[name],
                "month_return_pct": round(ret * 100, 2),
                "eligible": ret >= MIN_GROWTH,
            }
        )
    rows.sort(key=lambda r: r["month_return_pct"], reverse=True)
    return rows


def jev_score(name: str, ticker: str, prior_ret_pct: float, df: pd.DataFrame, month_end: pd.Timestamp) -> float:
    from typesafe_sdk import Noul, TypeSafeClient

    hist = df.loc[: month_end - pd.Timedelta(days=1)].tail(21)
    closes = hist["Close"].astype(float)
    vol = float(closes.pct_change().std()) if len(closes) > 5 else 0.0
    last = float(closes.iloc[-1]) if len(closes) else 0.0
    state = (
        f"NSE stock {name} ({ticker}). "
        f"Prior calendar-month return: {prior_ret_pct:.2f}%. "
        f"Recent ~1m close volatility (std of daily returns): {vol*100:.2f}%. "
        f"Last close before entry month: {last:.2f}. "
        f"Strategy: equal-weight monthly momentum, hold about one month, hard -10% stop, "
        f"0.25% transaction cost each side. Indian cash equities."
    )
    with TypeSafeClient(model=JEV_MODEL) as client:
        resp = client.system_one(
            state=state,
            questions={
                "approve_buy": Noul(
                    instructions=(
                        "For a short-term monthly momentum sleeve, should we BUY this name "
                        "for the upcoming month? Answer yes only if continuation odds look "
                        "reasonable versus crash/gap risk given the -10% stop."
                    )
                )
            },
        )
    return float(resp.answers["approve_buy"].noul)


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    months = month_starts_back(2)
    # download from a bit before first month through tomorrow
    dl_start = (months[0][0] - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    dl_end = (pd.Timestamp.now() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"As of {datetime.now(timezone.utc).isoformat()}")
    print(f"Downloading Yahoo {dl_start} -> {dl_end} for {len(STOCKS_MAP)} names ...")
    all_data = download_all(dl_start, dl_end)
    print(f"Loaded {len(all_data)} series\n")

    month_reports = []
    for start, end, label in months:
        rows = eligible_for_month(all_data, start, end)
        picks = [r for r in rows if r["eligible"]]
        buy_month = end.strftime("%Y-%m")
        print("=" * 72)
        print(f"ALGO month {label} (≥{MIN_GROWTH*100:.0f}% → buy list for {buy_month})")
        print("=" * 72)
        if not picks:
            print("  (no names ≥ 10%)")
        else:
            for r in picks:
                print(f"  {r['name']:<28} {r['month_return_pct']:>7.2f}%  {r['ticker']}")
        print(f"  Eligible count: {len(picks)} / {len(rows)}")
        month_reports.append(
            {
                "signal_month": label,
                "buy_for_month": buy_month,
                "eligible": picks,
                "all_ranked": rows,
            }
        )

    # Jev on latest month's eligible (most actionable)
    latest = month_reports[-1]
    key = load_typesafe_key()
    scored = []
    jev_error = None
    if not latest["eligible"]:
        print("\nNo eligible names in latest month — nothing to score with Jev.")
    elif not key:
        jev_error = "TYPESAFE_API_KEY missing. Add it to bot_secrets.env and re-run."
        print(f"\n!! {jev_error}")
    else:
        print("\n" + "=" * 72)
        print(f"JEV scores for {latest['buy_for_month']} buy list (from {latest['signal_month']})")
        print("=" * 72)
        month_end = pd.Timestamp(latest["buy_for_month"] + "-01")  # start of buy month = end of signal month
        for r in latest["eligible"]:
            try:
                noul = jev_score(
                    r["name"],
                    r["ticker"],
                    r["month_return_pct"],
                    all_data[r["name"]],
                    month_end,
                )
            except Exception as e:
                noul = None
                print(f"  {r['name']}: Jev error {e}")
            decisions = {f"buy_ge_{t}": (noul is not None and noul >= t) for t in JEV_THRESHOLDS}
            row = {
                **r,
                "jev_noul": None if noul is None else round(noul, 4),
                **{k: bool(v) for k, v in decisions.items()},
                "decision_default_0.55": (
                    "BUY" if noul is not None and noul >= 0.55 else ("SKIP" if noul is not None else "ERROR")
                ),
            }
            scored.append(row)
            if noul is not None:
                print(
                    f"  {r['name']:<28} ret={r['month_return_pct']:>6.2f}%  "
                    f"jev={noul:.2f}  → {row['decision_default_0.55']}"
                )

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "min_growth": MIN_GROWTH,
        "jev_model": JEV_MODEL,
        "jev_thresholds": list(JEV_THRESHOLDS),
        "months": [
            {
                "signal_month": m["signal_month"],
                "buy_for_month": m["buy_for_month"],
                "eligible": m["eligible"],
            }
            for m in month_reports
        ],
        "latest_buy_month": latest["buy_for_month"],
        "jev_scores": scored,
        "jev_error": jev_error,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nSaved {OUT_JSON}")

    if scored:
        print("\n" + "=" * 72)
        print("RECOMMENDED (algo ≥10% AND Jev ≥ 0.55)")
        print("=" * 72)
        rec = [s for s in scored if s.get("decision_default_0.55") == "BUY"]
        if not rec:
            print("  (none at 0.55 — consider 0.45/0.50 thresholds in JSON)")
        for s in rec:
            print(f"  {s['name']:<28} ret={s['month_return_pct']}%  jev={s['jev_noul']}")


if __name__ == "__main__":
    main()
