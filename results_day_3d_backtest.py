#!/usr/bin/env python3
"""
Results-day event backtest (Yahoo earnings dates + daily OHLC).

Rules:
  - On each Yahoo earnings/results date for a stock, buy at that day's Open
    (or next trading session if the date is not a session).
  - Hold up to 3 trading sessions (entry day = day 1).
  - Exit earlier if Close or Low implies -10% from entry (fill at entry * 0.90).
  - Otherwise exit at Close of the 3rd trading session.
  - 0.25% cost each side; 20% STCG drag each March on running realized profit.
  - Concurrent trades: each new event uses up to EQUAL_SHARE of current equity
    from available cash (default 1/5). Unused cash stays idle.

ETFs / names without earnings dates are skipped.

Period: 2021-01-01 -> 2026-09-01 (same as monthly churn tests).
"""

from __future__ import annotations

import json
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf

warnings.simplefilter(action="ignore", category=FutureWarning)

TRANSACTION_COST = 0.0025
STCG_TAX_RATE = 0.20
INITIAL_CAPITAL = 100_000.0
START_DATE = "2021-01-01"
END_DATE = "2026-09-01"
HOLD_SESSIONS = 3  # including entry day
STOP_PCT = 0.10
MAX_CONCURRENT_SLOTS = 5  # each new trade targets ~equity/5 from cash
EARNINGS_LIMIT = 48

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"
SUMMARY_PATH = ARTIFACTS / "results_day_3d_comparison.json"
PLOT_PATH = ARTIFACTS / "results_day_3d_comparison.png"
EVENTS_PATH = ARTIFACTS / "results_day_events_cache.json"

# Equity names only (skip ETFs — no results day)
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
    "Poly Medicure": "POLYMED.NS",
    "Rashi Peripherals": "RPTECH.NS",
    "RR Kabel": "RRKABEL.NS",
    "SKY Gold": "SKYGOLD.NS",
    "Uno Minda": "UNOMINDA.NS",
    "Varun Beverages": "VBL.NS",
    "Bharti Airtel": "BHARTIARTL.NS",
    "HDFC Bank": "HDFCBANK.NS",
    "HDFC Life": "HDFCLIFE.NS",
    "Hindustan Unilever": "HINDUNILVR.NS",
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


@dataclass
class Position:
    name: str
    entry_date: pd.Timestamp
    entry_price: float
    shares: float
    sessions_held: int = 1  # entry day counts as 1


def download_prices(start: str, end: str) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for name, ticker in STOCKS_MAP.items():
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
        if df is None or df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        out[name] = df
    return out


def fetch_earnings_events(force: bool = False) -> dict[str, list[str]]:
    """Return {name: [ISO date strings]} for earnings in [START_DATE, END_DATE)."""
    if EVENTS_PATH.exists() and not force:
        return json.loads(EVENTS_PATH.read_text(encoding="utf-8"))

    events: dict[str, list[str]] = {}
    start = pd.Timestamp(START_DATE)
    end = pd.Timestamp(END_DATE)
    for name, ticker in STOCKS_MAP.items():
        dates: list[str] = []
        try:
            df = yf.Ticker(ticker).get_earnings_dates(limit=EARNINGS_LIMIT)
            time.sleep(0.15)
            if df is None or df.empty:
                events[name] = []
                print(f"  {name}: no earnings dates")
                continue
            idx = df.index
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_convert(None)
            for ts in idx:
                d = pd.Timestamp(ts).normalize()
                if start <= d < end:
                    dates.append(d.strftime("%Y-%m-%d"))
            # unique sorted
            dates = sorted(set(dates))
            events[name] = dates
            print(f"  {name}: {len(dates)} events")
        except Exception as e:
            events[name] = []
            print(f"  {name}: error {e}")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    EVENTS_PATH.write_text(json.dumps(events, indent=2), encoding="utf-8")
    return events


def next_session(data: pd.DataFrame, day: pd.Timestamp) -> pd.Timestamp | None:
    future = data.index[data.index >= day]
    if len(future) == 0:
        return None
    return pd.Timestamp(future[0]).normalize()


def session_after(data: pd.DataFrame, day: pd.Timestamp, n: int) -> pd.Timestamp | None:
    """n=0 => same session day if exists / next; n=1 => following session, etc."""
    base = next_session(data, day)
    if base is None:
        return None
    loc = data.index.get_indexer([base], method=None)[0]
    if loc < 0:
        # exact match after normalize
        locs = data.index.get_indexer([base], method="pad")
        # find position
        pos = data.index.searchsorted(base)
        if pos >= len(data.index) or data.index[pos] != base:
            return None
        loc = pos
    target = loc + n
    if target >= len(data.index):
        return None
    return pd.Timestamp(data.index[target]).normalize()


def buy_and_hold(all_data: dict[str, pd.DataFrame]) -> dict:
    valid = [n for n, d in all_data.items() if not d.empty]
    alloc = INITIAL_CAPITAL / len(valid)
    end_val = 0.0
    for name in valid:
        data = all_data[name]
        units = alloc / float(data["Open"].iloc[0])
        end_val += units * float(data["Close"].iloc[-1])
    years = (pd.to_datetime(END_DATE) - pd.to_datetime(START_DATE)).days / 365.25
    ret = ((end_val - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    cagr = (((end_val / INITIAL_CAPITAL) ** (1 / years)) - 1) * 100
    return {
        "label": "Buy & Hold (equities in map)",
        "final_value": round(end_val, 2),
        "total_return_pct": round(ret, 2),
        "cagr_pct": round(cagr, 2),
        "trades": None,
    }


def benchmark_return(ticker: str) -> float:
    df = yf.download(ticker, start=START_DATE, end=END_DATE, progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    return ((float(df[col].iloc[-1]) - float(df[col].iloc[0])) / float(df[col].iloc[0])) * 100


def run_backtest(all_data: dict[str, pd.DataFrame], events: dict[str, list[str]]) -> dict:
    # Build calendar and event map: date -> list of names
    calendar = None
    for df in all_data.values():
        calendar = df.index if calendar is None else calendar.union(df.index)
    calendar = calendar.sort_values()
    calendar = calendar[(calendar >= START_DATE) & (calendar < END_DATE)]

    events_by_day: dict[pd.Timestamp, list[str]] = {}
    skipped_no_price = 0
    total_events = 0
    for name, date_strs in events.items():
        if name not in all_data:
            continue
        for ds in date_strs:
            total_events += 1
            day = pd.Timestamp(ds)
            sess = next_session(all_data[name], day)
            if sess is None or sess >= pd.Timestamp(END_DATE):
                skipped_no_price += 1
                continue
            events_by_day.setdefault(sess, []).append(name)

    cash = INITIAL_CAPITAL
    positions: list[Position] = []
    realized_year_pnl = 0.0
    trade_log: list[dict] = []
    track_dates = [pd.Timestamp(START_DATE)]
    track_values = [INITIAL_CAPITAL]
    taxed_years: set[int] = set()

    def mtm_equity(asof: pd.Timestamp) -> float:
        total = cash
        for p in positions:
            data = all_data[p.name]
            if asof in data.index:
                px = float(data.loc[asof, "Close"])
            else:
                # last available close <= asof
                hist = data.loc[:asof]
                px = float(hist["Close"].iloc[-1]) if not hist.empty else p.entry_price
            total += p.shares * px
        return total

    def close_position(p: Position, exit_date: pd.Timestamp, exit_price: float, reason: str) -> None:
        nonlocal cash, realized_year_pnl
        proceeds = p.shares * exit_price * (1 - TRANSACTION_COST)
        # cost basis approx: shares bought with entry notional after buy cost
        cost_basis = p.shares * p.entry_price / (1 - TRANSACTION_COST) * (1 - TRANSACTION_COST)
        # simpler PnL: proceeds - capital allocated at entry
        capital_in = p.shares * p.entry_price / (1 - TRANSACTION_COST)
        # shares = (alloc * (1-cost))/entry => alloc = shares*entry/(1-cost)
        alloc = p.shares * p.entry_price / (1 - TRANSACTION_COST)
        pnl = proceeds - alloc
        cash += proceeds
        realized_year_pnl += pnl
        trade_log.append(
            {
                "name": p.name,
                "entry": p.entry_date.strftime("%Y-%m-%d"),
                "exit": exit_date.strftime("%Y-%m-%d"),
                "entry_px": round(p.entry_price, 4),
                "exit_px": round(exit_price, 4),
                "pnl": round(pnl, 2),
                "return_pct": round(100.0 * pnl / alloc, 2) if alloc else 0.0,
                "reason": reason,
            }
        )

    for i, day in enumerate(calendar):
        day = pd.Timestamp(day).normalize()

        # 1) Manage open positions: stop or time exit
        still_open: list[Position] = []
        for p in positions:
            data = all_data[p.name]
            if day not in data.index:
                still_open.append(p)
                continue
            row = data.loc[day]
            o = float(row["Open"])
            h = float(row["High"])
            lo = float(row["Low"])
            c = float(row["Close"])

            # On days after entry, increment session count at start of day processing
            if day > p.entry_date:
                p.sessions_held += 1

            stopped = False
            if day == p.entry_date:
                # same-day stop if Low hits -10% from entry open
                if lo <= p.entry_price * (1 - STOP_PCT):
                    close_position(p, day, p.entry_price * (1 - STOP_PCT), "stop")
                    stopped = True
            else:
                if lo <= p.entry_price * (1 - STOP_PCT):
                    close_position(p, day, p.entry_price * (1 - STOP_PCT), "stop")
                    stopped = True

            if stopped:
                continue

            if p.sessions_held >= HOLD_SESSIONS:
                close_position(p, day, c, "time")
                continue

            still_open.append(p)
        positions = still_open

        # 2) New entries from earnings today
        names_today = [n for n in events_by_day.get(day, []) if n in all_data and day in all_data[n].index]
        # avoid doubling into same name if already open
        open_names = {p.name for p in positions}
        names_today = [n for n in names_today if n not in open_names]

        if names_today and cash > 1:
            equity = mtm_equity(day)
            slot = equity / MAX_CONCURRENT_SLOTS
            # equal among today's new names, each capped by slot and cash
            per = min(slot, cash / len(names_today))
            for name in names_today:
                if cash < 1 or per < 1:
                    break
                alloc = min(per, cash)
                entry_price = float(all_data[name].loc[day, "Open"])
                if entry_price <= 0:
                    continue
                shares = (alloc * (1 - TRANSACTION_COST)) / entry_price
                cash -= alloc
                positions.append(
                    Position(name=name, entry_date=day, entry_price=entry_price, shares=shares, sessions_held=1)
                )
                # same-day stop check after entry
                lo = float(all_data[name].loc[day, "Low"])
                c = float(all_data[name].loc[day, "Close"])
                if lo <= entry_price * (1 - STOP_PCT):
                    p = positions.pop()
                    close_position(p, day, entry_price * (1 - STOP_PCT), "stop")
                elif HOLD_SESSIONS <= 1:
                    p = positions.pop()
                    close_position(p, day, c, "time")

        # 3) March tax on last March session
        is_last_march = day.month == 3 and (
            i == len(calendar) - 1 or pd.Timestamp(calendar[i + 1]).month != 3
        )
        if is_last_march and day.year not in taxed_years:
            if realized_year_pnl > 0:
                tax = realized_year_pnl * STCG_TAX_RATE
                cash -= tax
            realized_year_pnl = 0.0
            taxed_years.add(day.year)

        track_dates.append(day)
        track_values.append(mtm_equity(day))

    # Flatten leftovers at last close
    if positions:
        last = pd.Timestamp(calendar[-1]).normalize()
        for p in list(positions):
            data = all_data[p.name]
            hist = data.loc[:last]
            px = float(hist["Close"].iloc[-1])
            close_position(p, last, px, "forced_end")
        positions = []
        track_values[-1] = cash

    if realized_year_pnl > 0:
        cash -= realized_year_pnl * STCG_TAX_RATE
        track_values[-1] = cash

    years = (pd.to_datetime(END_DATE) - pd.to_datetime(START_DATE)).days / 365.25
    final_value = cash
    total_return = ((final_value - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    cagr = (((final_value / INITIAL_CAPITAL) ** (1 / years)) - 1) * 100 if final_value > 0 else -100.0
    wins = sum(1 for t in trade_log if t["pnl"] > 0)
    stops = sum(1 for t in trade_log if t["reason"] == "stop")

    return {
        "label": "Results day → 3 sessions / -10% SL",
        "final_value": round(final_value, 2),
        "total_return_pct": round(total_return, 2),
        "cagr_pct": round(cagr, 2),
        "trades": len(trade_log),
        "win_rate_pct": round(100.0 * wins / len(trade_log), 2) if trade_log else 0.0,
        "stop_exits": stops,
        "time_exits": sum(1 for t in trade_log if t["reason"] == "time"),
        "earnings_events_listed": total_events,
        "skipped_no_price": skipped_no_price,
        "names_with_events": sum(1 for v in events.values() if v),
        "track_dates": [d.isoformat() for d in track_dates],
        "track_values": track_values,
        "trade_log": trade_log,
    }


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    print("Downloading prices ...")
    all_data = download_prices(START_DATE, END_DATE)
    print(f"Loaded {len(all_data)} price series")

    print("Fetching Yahoo earnings/results dates ...")
    events = fetch_earnings_events(force=not EVENTS_PATH.exists())
    n_events = sum(len(v) for v in events.values())
    print(f"Total listed events in window: {n_events}")

    result = run_backtest(all_data, events)
    bah = buy_and_hold(all_data)
    nifty50 = round(benchmark_return("^NSEI"), 2)
    nifty500 = round(benchmark_return("^CRSLDX"), 2)

    # Optional: load monthly baseline from prior artifact if present
    monthly = None
    monthly_path = ARTIFACTS / "daily_open_close_comparison.json"
    if monthly_path.exists():
        prev = json.loads(monthly_path.read_text(encoding="utf-8"))
        monthly = prev.get("monthly_hold")

    def slim(d: dict) -> dict:
        return {k: v for k, v in d.items() if k not in ("track_dates", "track_values", "trade_log")}

    summary = {
        "period": f"{START_DATE} to {END_DATE}",
        "initial_capital": INITIAL_CAPITAL,
        "rules": {
            "entry": "Open on Yahoo earnings date (or next session)",
            "exit": f"Close of session {HOLD_SESSIONS} or -{int(STOP_PCT*100)}% stop first",
            "costs": TRANSACTION_COST,
            "tax_march": STCG_TAX_RATE,
            "position_sizing": f"up to equity/{MAX_CONCURRENT_SLOTS} per new trade from cash",
        },
        "results_day_strategy": slim(result),
        "buy_and_hold": bah,
        "monthly_hold_from_prior_artifact": monthly,
        "nifty50_total_return_pct": nifty50,
        "nifty500_total_return_pct": nifty500,
        "sample_trades": result["trade_log"][:15],
    }
    # save full trade log separately
    (ARTIFACTS / "results_day_3d_trades.json").write_text(
        json.dumps(result["trade_log"], indent=2), encoding="utf-8"
    )
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    plt.figure(figsize=(12, 6))
    plt.plot(pd.to_datetime(result["track_dates"]), result["track_values"], label=result["label"])
    plt.title("Results-day Open → 3 sessions / -10% SL")
    plt.xlabel("Date")
    plt.ylabel("Portfolio value (INR)")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(PLOT_PATH, dpi=120)
    plt.close()

    print("\n" + "=" * 72)
    print("RESULTS-DAY BACKTEST SUMMARY")
    print("=" * 72)
    print(pd.Series(slim(result)).to_string())
    print(f"\nBuy & Hold: {bah}")
    if monthly:
        print(f"Monthly hold (prior): {monthly}")
    print(f"Nifty 50: {nifty50}% | Nifty 500: {nifty500}%")
    print(f"Saved: {SUMMARY_PATH}")
    print(f"Plot:  {PLOT_PATH}")
    print("=" * 72)


if __name__ == "__main__":
    main()
