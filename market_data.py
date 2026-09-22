"""
BreakoutBot — Binance public market data.

Every 5m/4h frame the live engine trades on comes through here. Both fetchers
return CLOSED bars only: the still-forming candle is dropped, because
its volume ≈ 0 corrupts volume_ratio and the composite score, and because the
backtest iterates closed bars exclusively — the parity contract in backtest.py
depends on the two agreeing.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import ccxt
import pandas as pd

from config import TIMEFRAME

FETCH_BARS = 200   # rolling history window per symbol (enough for all indicators)

# Single shared public exchange — avoids creating 23+ instances per bar
# (each new instance triggers an exchangeInfo call → rate-limit cascade)
_PUBLIC_EX = ccxt.binanceusdm({"enableRateLimit": True})


_OHLCV_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def _to_frame(raw: list) -> pd.DataFrame:
    """ccxt's list-of-lists → a sorted, de-duplicated, typed OHLCV frame."""
    df = pd.DataFrame(raw, columns=_OHLCV_COLUMNS)
    df = df.astype({c: float for c in _OHLCV_COLUMNS[1:]})
    df["ts"] = df["ts"].astype(int)
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def _drop_forming_bar(df: pd.DataFrame, span_ms: int) -> pd.DataFrame:
    """Drop the candle still being built at `now`."""
    current = (_PUBLIC_EX.milliseconds() // span_ms) * span_ms
    if len(df) and int(df["ts"].iloc[-1]) >= current:
        df = df.iloc[:-1]
    return df


def fetch_recent(symbol: str, bars: int = FETCH_BARS) -> pd.DataFrame:
    """Fetch the most recent `bars` closed 5-min candles (Binance Futures public)."""
    since = _PUBLIC_EX.milliseconds() - (bars + 20) * 300 * 1000
    raw   = _PUBLIC_EX.fetch_ohlcv(symbol, TIMEFRAME, since=since, limit=bars + 20)
    df    = _to_frame(raw)
    # Drop the still-forming candle: when polled at boundary+2s the last row is the
    # CURRENT (incomplete) bar — its volume ≈ 0 corrupts volume_ratio (→ vol_ok
    # always fails → 100% CONFIRM_FAIL) AND the composite score. Use only fully
    # closed bars, matching the backtest which iterates closed bars exclusively.
    df = _drop_forming_bar(df, 300 * 1000)
    return df.tail(bars).reset_index(drop=True)


def fetch_4h(symbol: str, bars: int = 260) -> pd.DataFrame:
    """Fetch recent 4h candles (public) for the 200-MA regime classifier."""
    span  = 4 * 3600 * 1000
    since = _PUBLIC_EX.milliseconds() - (bars + 5) * span
    raw   = _PUBLIC_EX.fetch_ohlcv(symbol, "4h", since=since, limit=bars + 5)
    # The regime MA must see closed bars only.
    df    = _drop_forming_bar(_to_frame(raw), span)
    return df.reset_index(drop=True)


def wait_for_bar_close(bar_seconds: int = 300) -> datetime:
    """Sleep until the next 5-min bar boundary + 2-second exchange latency buffer."""
    now      = time.time()
    next_bar = (int(now) // bar_seconds + 1) * bar_seconds
    sleep    = next_bar - now + 2.0
    if sleep > 0:
        time.sleep(sleep)
    return datetime.fromtimestamp(next_bar, tz=timezone.utc)
