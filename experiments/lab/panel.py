"""Panel data: 23 coins x any timeframe, aligned into matrices.

A DELIBERATE BREAK FROM THE EXISTING ENGINE
-------------------------------------------
`paper_bb`/`backtest` walk one symbol at a time through a state machine. That
shape makes three choices invisible by making them unaskable: the 5m timeframe,
the single-asset thesis, and which coins are in the universe. All three were
inherited rather than tested, and two of them were measured today and found
wanting — friction is 0.0015/sl_frac (crushing at 5m stop distances), and
ranking coins by past return carries no out-of-sample information (PBO 0.486).

So this lab represents the market as matrices instead: close[t, s]. A strategy
returns a weight matrix w[t, s] and the engine lags it one bar. Timeframe becomes
an argument, cross-sectional strategies become expressible, and no strategy can
see its own bar's close, because the lag is applied by the engine rather than
remembered by the author.

Nothing here imports the trading engine. It shares only the cached OHLCV.
"""
from __future__ import annotations

import glob
import json
import os

import numpy as np
import pandas as pd

CACHE = "backtests/data"
# 5m source bars -> target frame. Anything coarser is a pure resample.
TF_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


def _cache_path(sym: str, days: int, warm: int = 40) -> str:
    """Self-generated OHLCV frames written by this repo's own Binance fetch;
    nothing external is ever unpickled."""
    return f"{CACHE}/{sym}_{days}d+{warm}w.pkl"


def available(days: int) -> list[str]:
    out = []
    for p in sorted(glob.glob(f"{CACHE}/*_{days}d+40w.pkl")):
        out.append(os.path.basename(p).split("_")[0])
    return out


def resample(df5: pd.DataFrame, tf: str) -> pd.DataFrame:
    """5m OHLCV -> `tf`, indexed by bar OPEN time in UTC.

    Bins are left-closed and left-labelled, which is what makes the engine's
    one-bar lag mean what it says: the bar labelled T covers [T, T+tf), so a
    weight derived from it can only be traded from T+tf onward.
    """
    if tf == "5m":
        d = df5.copy()
        d.index = pd.to_datetime(d["ts"], unit="ms", utc=True)
        return d[["open", "high", "low", "close", "volume"]]
    mins = TF_MINUTES[tf]
    d = df5.copy()
    d.index = pd.to_datetime(d["ts"], unit="ms", utc=True)
    o = d.resample(f"{mins}min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"})
    return o.dropna()


class Panel:
    """Aligned matrices for a set of symbols on one timeframe.

    `mask` marks cells where the coin actually traded. A coin listed mid-window
    has NaNs before its first bar, and treating those as zero returns would let
    a strategy "hold" an asset that did not exist — the quiet way a panel
    backtest invents history.
    """

    def __init__(self, symbols: list[str], close: np.ndarray, high: np.ndarray,
                 low: np.ndarray, volume: np.ndarray, index: pd.DatetimeIndex):
        self.symbols = symbols
        self.close, self.high, self.low, self.volume = close, high, low, volume
        self.index = index
        self.mask = ~np.isnan(close)
        # Funding is a return component the price matrix cannot express: a short
        # perp RECEIVES it while it is positive. None until load(with_funding=1).
        self.funding: np.ndarray | None = None
        with np.errstate(invalid="ignore", divide="ignore"):
            prev = np.vstack([np.full((1, close.shape[1]), np.nan), close[:-1]])
            self.ret = close / prev - 1.0
        self.ret[~np.isfinite(self.ret)] = 0.0

    @property
    def shape(self) -> tuple[int, int]:
        return self.close.shape

    def __repr__(self) -> str:
        a, b = self.index[0], self.index[-1]
        return (f"Panel({len(self.symbols)} sym x {self.shape[0]} bar, "
                f"{a:%Y-%m-%d}→{b:%Y-%m-%d})")


def load_funding(symbols: list[str], index: pd.DatetimeIndex,
                 tf: str) -> np.ndarray | None:
    """Funding rates summed into each bar of `index`.

    Binance settles funding every 8h on most pairs and every 4h on some, so the
    rate is NOT a per-bar quantity and cannot simply be reindexed — it has to be
    SUMMED over the interval each bar covers. Doing that also makes the result
    independent of which settlement schedule a pair happens to use.

    Bars are left-labelled, so bar T owns every settlement in [T, T+tf). A
    position held during bar T pays (or receives) exactly those.
    """
    mins = TF_MINUTES[tf]
    step = pd.Timedelta(minutes=mins)
    cols = []
    any_found = False
    for s in symbols:
        p = f"experiments/lab/funding/{s}.json"
        if not os.path.exists(p):
            cols.append(np.zeros(len(index)))
            continue
        any_found = True
        recs = json.load(open(p))["records"]
        ts = pd.to_datetime([r["ts"] for r in recs], unit="ms", utc=True)
        ser = pd.Series([r["rate"] for r in recs], index=ts).sort_index()
        # Left-closed bins aligned to the panel's own grid.
        binned = ser.groupby(ser.index.floor(step)).sum()
        cols.append(binned.reindex(index).fillna(0.0).to_numpy(float))
    return np.column_stack(cols) if any_found else None


def load(symbols: list[str], days: int, tf: str, with_funding: bool = False) -> Panel:
    frames = {}
    for s in symbols:
        p = _cache_path(s, days)
        if not os.path.exists(p):
            continue
        frames[s] = resample(pd.read_pickle(p), tf)
    if not frames:
        raise SystemExit(f"{CACHE}/ içinde {days}g veri yok")

    syms = sorted(frames)
    idx = frames[syms[0]].index
    for s in syms[1:]:
        idx = idx.union(frames[s].index)
    idx = idx.sort_values()

    def mat(col: str) -> np.ndarray:
        return np.column_stack([frames[s][col].reindex(idx).to_numpy(float)
                                for s in syms])

    pan = Panel(syms, mat("close"), mat("high"), mat("low"), mat("volume"), idx)
    if with_funding:
        pan.funding = load_funding(syms, idx, tf)
    return pan
