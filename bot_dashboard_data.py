"""Shared data layer for bot live dashboard (web + canvas)."""

from __future__ import annotations

import json
import re
from datetime import datetime, time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "bot_v2_state.json"
TRADES_FILE = ROOT / "Completed_Trades_V2.xlsx"
LOG_FILE = ROOT / "bot_v2_execution.log"
REFRESH_SEC = 15

TICKER_CONNECT = time(8, 0)
TICKER_DISCONNECT = time(15, 30)


def fmt_inr(value: float | None) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    sign = "+" if value >= 0 else "-"
    return f"{sign}₹{abs(value):,.0f}"


def fmt_price(value: float | None) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return f"₹{value:,.2f}"


def read_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    with STATE_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def read_trades() -> pd.DataFrame:
    if not TRADES_FILE.exists():
        return pd.DataFrame()
    df = pd.read_excel(TRADES_FILE)
    if "Exit Time" in df.columns:
        df["Exit Time"] = pd.to_datetime(df["Exit Time"], errors="coerce")
    if "Entry Time" in df.columns:
        df["Entry Time"] = pd.to_datetime(df["Entry Time"], errors="coerce")
    return df


def read_log_tail(n: int = 60) -> list[str]:
    if not LOG_FILE.exists():
        return []
    try:
        with LOG_FILE.open(encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [line.rstrip() for line in lines[-n:]]
    except OSError:
        return []


def bot_running(log_lines: list[str]) -> bool:
    if not log_lines:
        return False
    joined = "\n".join(log_lines[-80:])
    return bool(re.search(r"Bot running\. Entering keep-alive loop", joined))


def websocket_live(log_lines: list[str]) -> bool:
    tail = "\n".join(log_lines[-50:])
    if "Outside websocket hours" in tail:
        return False
    if "MARKET CLOSED (15:30)" in tail and "Ticker Connected" not in tail.split("MARKET CLOSED")[-1]:
        return False
    return bool(re.search(r"Ticker Connected", tail))


def stream_status(now: datetime | None = None, log_lines: list[str] | None = None) -> str:
    """Human label for Kite ticker stream state."""
    now = now or datetime.now()
    log_lines = log_lines or []
    if not bot_running(log_lines):
        return "stopped"
    if now.weekday() >= 5:
        return "weekend"
    t = now.time()
    if t < TICKER_CONNECT or t >= TICKER_DISCONNECT:
        return "after_hours"
    if websocket_live(log_lines):
        return "live"
    return "connecting"


def fetch_option_ltp(symbol: str | None) -> float | None:
    """Live option LTP via Kite REST (for MTM on open position)."""
    if not symbol:
        return None
    try:
        from kiteconnect import KiteConnect

        from bot_secrets import get_kite_api_key, load_secrets
        from kite_auth import read_access_token

        load_secrets()
        token = read_access_token()
        if not token:
            return None
        kite = KiteConnect(api_key=get_kite_api_key())
        kite.set_access_token(token)
        key = f"NFO:{symbol}"
        data = kite.ltp([key])
        if data and key in data:
            return float(data[key]["last_price"])
    except Exception:
        return None
    return None


def position_qty(pos: dict | None) -> int | None:
    if not pos:
        return None
    legs = pos.get("legs") or []
    if legs:
        return int(legs[0].get("qty") or 0) or None
    return None


def trade_row(row: pd.Series) -> dict:
    exit_time = row.get("Exit Time")
    entry_time = row.get("Entry Time")
    if pd.notna(exit_time):
        time_str = pd.Timestamp(exit_time).strftime("%d %b %H:%M")
    else:
        time_str = "—"
    if pd.notna(entry_time):
        entry_time_str = pd.Timestamp(entry_time).strftime("%d %b %H:%M")
    else:
        entry_time_str = "—"
    pnl = row.get("PnL Value")
    entry = row.get("Entry Price")
    exit_px = row.get("Exit Price")
    return {
        "time": time_str,
        "entry_time": entry_time_str,
        "type": str(row.get("Type", "")),
        "symbol": str(row.get("Symbol", "")),
        "entry_price": float(entry) if pd.notna(entry) else None,
        "exit_price": float(exit_px) if pd.notna(exit_px) else None,
        "entry_fmt": fmt_price(float(entry) if pd.notna(entry) else None),
        "exit_fmt": fmt_price(float(exit_px) if pd.notna(exit_px) else None),
        "pnl": float(pnl) if pd.notna(pnl) else None,
        "pnl_fmt": fmt_inr(float(pnl) if pd.notna(pnl) else None),
        "reason": str(row.get("Reason", "")),
    }


def weekly_pnl(df: pd.DataFrame, days: int = 5) -> tuple[list[str], list[float]]:
    if df.empty or "Exit Time" not in df.columns or "PnL Value" not in df.columns:
        return [], []
    work = df.copy()
    work["PnL Value"] = pd.to_numeric(work["PnL Value"], errors="coerce")
    work = work.dropna(subset=["Exit Time"])
    work["day"] = work["Exit Time"].dt.date
    daily = work.groupby("day", as_index=False)["PnL Value"].sum()
    daily = daily.sort_values("day").tail(days)
    labels = [pd.Timestamp(d).strftime("%d %b") for d in daily["day"]]
    values = [float(v) for v in daily["PnL Value"]]
    return labels, values


def collect_status() -> dict:
    now = datetime.now()
    state = read_state()
    df = read_trades()
    log_tail = read_log_tail(60)

    today = now.date()
    today_realized = 0.0
    cumulative = None
    today_trades: list[dict] = []
    recent_trades: list[dict] = []
    today_exit_count = 0

    if not df.empty and "PnL Value" in df.columns:
        df["PnL Value"] = pd.to_numeric(df["PnL Value"], errors="coerce")
        if "Cumulative PnL" in df.columns:
            cum = pd.to_numeric(df["Cumulative PnL"], errors="coerce")
            if cum.notna().any():
                cumulative = float(cum.dropna().iloc[-1])

        if "Exit Time" in df.columns:
            today_mask = df["Exit Time"].dt.date == today
            today_realized = float(df.loc[today_mask, "PnL Value"].sum(skipna=True))
            today_exit_count = int(today_mask.sum())
            today_df = df.loc[today_mask].sort_values("Exit Time", ascending=False)
            for _, row in today_df.head(20).iterrows():
                today_trades.append(trade_row(row))

        recent_df = df.sort_values("Exit Time", ascending=False).head(25)
        for _, row in recent_df.iterrows():
            recent_trades.append(trade_row(row))

    positions = state.get("positions") or []
    pos = positions[0] if positions else None
    pos_type = pos.get("type") if pos else "FLAT"
    entry_price = pos.get("entry_price") if pos else None
    symbol = pos.get("symbol") if pos else None
    qty = position_qty(pos)

    sl = state.get("spl") if pos_type == "LONG" else state.get("sph")
    sl_label = "SPL (LONG stop)" if pos_type == "LONG" else "SPH (SHORT stop)" if pos_type == "SHORT" else "—"

    mtm = None
    ltp = None
    if pos and symbol and entry_price is not None and qty:
        ltp = fetch_option_ltp(symbol)
        if ltp is not None:
            mtm = (ltp - float(entry_price)) * qty

    today_total = today_realized + (mtm or 0.0) if mtm is not None else today_realized

    weekly_labels, weekly_values = weekly_pnl(df)
    stream = stream_status(now, log_tail)

    return {
        "updated_at": now.strftime("%d %b %Y %H:%M:%S IST"),
        "refresh_sec": REFRESH_SEC,
        "bot_running": bot_running(log_tail),
        "websocket_live": websocket_live(log_tail),
        "stream_status": stream,
        "position": {
            "type": pos_type,
            "symbol": symbol,
            "strike": pos.get("strike") if pos else None,
            "entry_price": entry_price,
            "entry_price_fmt": fmt_price(float(entry_price) if entry_price is not None else None),
            "ltp": ltp,
            "ltp_fmt": fmt_price(ltp),
            "entry_time": str(pos.get("time", ""))[:19] if pos else None,
            "spot_at_entry": pos.get("spot_price") if pos else None,
            "expiry": pos.get("expiry") or state.get("expiry_date"),
            "qty": qty,
        },
        "levels": {
            "sph": state.get("sph"),
            "spl": state.get("spl"),
            "sl": sl,
            "sl_label": sl_label,
            "gap_sl_locked": state.get("gap_sl_locked"),
            "gap_sl_locked_value": state.get("gap_sl_locked_value"),
        },
        "pnl": {
            "today_realized": today_realized,
            "today_realized_fmt": fmt_inr(today_realized),
            "mtm": mtm,
            "mtm_fmt": fmt_inr(mtm),
            "today_total": today_total,
            "today_total_fmt": fmt_inr(today_total),
            "cumulative": cumulative,
            "cumulative_fmt": fmt_inr(cumulative),
            "today_exit_count": today_exit_count,
        },
        "today_trades": today_trades,
        "recent_trades": recent_trades,
        "weekly_labels": weekly_labels,
        "weekly_values": weekly_values,
        "log_tail": log_tail,
    }
