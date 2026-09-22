"""
BreakoutBot — historical OHLCV for the backtest.

Fetching and caching only: every frame a replay reads comes from here, and the
cache is what makes an experiment arm reproducible — `experiments/run_arm.sh`
pins a window on disk so a metric difference between two arms is the parameter
change and nothing else.

The cache directory is shared with bench.py, which uses the same file naming.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import ccxt
import pandas as pd

from config import REGIME_WARMUP_DAYS, TIMEFRAME

CACHE_DIR = "backtests/data"    # shared with bench.py (same file naming)


def fetch_history(symbol: str, days: int, silent: bool = False) -> pd.DataFrame:
    """Fetch historical OHLCV from Binance Futures (paginated, 1000 bars/call)."""
    exchange   = ccxt.binanceusdm({"enableRateLimit": True})
    bar_sec    = 300           # 5m = 300 seconds
    limit      = 1000
    total      = days * 24 * 3600 // bar_sec
    all_bars:  list = []
    since      = exchange.milliseconds() - total * bar_sec * 1000

    if not silent:
        print(f"  Fetching {days}d of {symbol} 5m data ({total:,} bars)…", flush=True)
    while len(all_bars) < total:
        batch = exchange.fetch_ohlcv(symbol, TIMEFRAME, since=since, limit=limit)
        if not batch:
            break
        all_bars.extend(batch)
        since = batch[-1][0] + bar_sec * 1000
        if not silent:
            print(f"  … {len(all_bars):,}/{total:,} bars", end="\r", flush=True)
        if len(batch) < limit:
            break
        time.sleep(0.25)
    if not silent:
        print()

    df = pd.DataFrame(all_bars, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.astype({c: float for c in ["open", "high", "low", "close", "volume"]})
    df["ts"] = df["ts"].astype(int)
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    if not silent:
        print(f"  ✅ Fetched {len(df):,} bars", flush=True)
    return df

# ── Data loading ──────────────────────────────────────────────────────────────

def load_frames(tokens: list[str], days: int,
                use_cache: bool = False) -> dict[str, pd.DataFrame]:
    """Fetch `days` of 5m data PLUS the REGIME_WARMUP_DAYS prefix, per token.

    The prefix is not optional. regime.score_series_4h needs 210 CLOSED 4h bars
    (~35d) before it can label anything; without them every bar reads NEUTRAL, and
    LONG_SIZE_MULT["NEUTRAL"] is 0.0 — so momentum trades nothing for the first
    ~35 days of the window and the MR sleeve inherits the month (M1). This applies
    to the BTC frame too: the CLI used to fetch BTC for `days` only, which left the
    BTC half of the regime blend pinned at 0 for the first 35 days of every run.

    Cache files share bench.py's naming, so the two harnesses reuse each other's
    downloads. Caching is opt-in here: an uncached run always means "the last N
    days as of now".
    """
    out: dict[str, pd.DataFrame] = {}
    if use_cache:
        os.makedirs(CACHE_DIR, exist_ok=True)
    for sym in tokens:
        path = f"{CACHE_DIR}/{sym}_{days}d+{REGIME_WARMUP_DAYS}w.pkl"
        if use_cache and os.path.exists(path):
            try:
                out[sym] = pd.read_pickle(path)
                print(f"  [cache] {sym}: {len(out[sym]):,} bars", flush=True)
                continue
            except Exception as exc:
                print(f"  ⚠️  {sym}: cache unreadable ({exc}) — refetching",
                      flush=True)
        df = fetch_history(sym, days + REGIME_WARMUP_DAYS)
        want = days + REGIME_WARMUP_DAYS
        if len(df) > 1:
            got = (int(df["ts"].iloc[-1]) - int(df["ts"].iloc[0])) / 86_400_000
            if got < want * 0.95:
                first = datetime.fromtimestamp(int(df["ts"].iloc[0]) / 1000,
                                               tz=timezone.utc)
                print(f"  ⚠️  {sym}: asked {want}d, the exchange only has {got:.0f}d "
                      f"(first bar {first:%Y-%m-%d}). It trades only where it has "
                      f"data — see the coverage column in the report.", flush=True)
        if use_cache:
            try:
                df.to_pickle(path)
            except Exception as exc:
                print(f"  ⚠️  {sym}: could not cache ({exc})", flush=True)
        out[sym] = df
    return out

