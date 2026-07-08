"""
Indicator helpers — adapted from CryptoSentinelTrader backtest/engine.py
Pure pandas/numpy, no external dependencies beyond numpy+pandas.
All functions take pd.Series or raw floats and return a float.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ── Core technical indicators ─────────────────────────────────────────────────

def rsi(closes: pd.Series, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    delta = closes.diff().iloc[-(period + 1):]
    gain  = delta.clip(lower=0).mean()
    loss  = (-delta.clip(upper=0)).mean()
    if loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


def ema(closes: pd.Series, period: int) -> float:
    if len(closes) < period:
        return float(closes.mean())
    return float(closes.ewm(span=period, adjust=False).mean().iloc[-1])


def macd_hist(closes: pd.Series) -> float:
    if len(closes) < 35:
        return 0.0
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    sig = macd_line.ewm(span=9, adjust=False).mean()
    return float((macd_line - sig).iloc[-1])


def atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    tr = pd.concat([
        highs - lows,
        (highs - closes.shift()).abs(),
        (lows  - closes.shift()).abs(),
    ], axis=1).max(axis=1)
    return float(tr.ewm(span=period, adjust=False).mean().iloc[-1])


def adx(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14) -> float:
    if len(closes) < period + 2:
        return 20.0
    up       = highs.diff()
    down     = -lows.diff()
    plus_dm  = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = pd.concat([
        highs - lows,
        (highs - closes.shift()).abs(),
        (lows  - closes.shift()).abs(),
    ], axis=1).max(axis=1)
    atr_s    = tr.ewm(span=period, adjust=False).mean()
    safe     = atr_s.replace(0, 1e-9)
    plus_di  = 100 * plus_dm.ewm(span=period, adjust=False).mean()  / safe
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / safe
    dsum     = (plus_di + minus_di).replace(0, 1e-9)
    dx       = 100 * (plus_di - minus_di).abs() / dsum
    return float(dx.ewm(span=period, adjust=False).mean().iloc[-1])


def bollinger(closes: pd.Series, period: int = 20, std_mult: float = 2.0
              ) -> tuple[float, float, float, float]:
    """Returns (bb_upper, bb_lower, bb_position, bb_width_pct).
    bb_position = (close - lower) / (upper - lower) in [0..1].
    """
    if len(closes) < period:
        p = float(closes.iloc[-1])
        return p, p, 0.5, 0.0
    sma   = float(closes.iloc[-period:].mean())
    std   = float(closes.iloc[-period:].std())
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    price = float(closes.iloc[-1])
    width = upper - lower
    pos   = (price - lower) / width if width > 0 else 0.5
    return upper, lower, pos, (width / sma * 100 if sma > 0 else 0.0)


def hurst_exponent(closes: pd.Series, max_lag: int = 20) -> float:
    """Estimate Hurst exponent via rescaled-range analysis.
    H > 0.55 → trending, H < 0.45 → mean-reverting, ~0.5 → random walk.
    Returns 0.5 (random walk) as default when insufficient data.
    """
    if len(closes) < max_lag + 5:
        return 0.5
    prices  = np.log(closes.values.astype(float))
    diff    = np.diff(prices, n=1)     # compute ONCE outside the lag loop
    lags    = range(2, max_lag)
    rs_vals = []
    for lag in lags:
        chunks = len(diff) // lag
        if chunks < 2:
            continue
        rs_list = []
        for c in range(chunks):
            seg      = diff[c * lag: (c + 1) * lag]
            mean_seg = seg.mean()
            dev      = np.cumsum(seg - mean_seg)
            r        = dev.max() - dev.min()
            s        = seg.std()
            if s > 0:
                rs_list.append(r / s)
        if rs_list:
            rs_vals.append(np.mean(rs_list))
    if len(rs_vals) < 3:
        return 0.5
    try:
        lags_arr = np.log(np.arange(2, 2 + len(rs_vals)))
        rs_arr   = np.log(rs_vals)
        slope    = np.polyfit(lags_arr, rs_arr, 1)[0]
        return float(np.clip(slope, 0.0, 1.0))
    except Exception:
        return 0.5


# ── BTC cross-asset helpers ───────────────────────────────────────────────────

def btc_4h_return(btc_closes: pd.Series, window: int = 48) -> float:
    """BTC rolling 4h return (last `window` 5m bars).
    Positive = BTC trending up, Negative = BTC selling off.
    """
    if len(btc_closes) < window + 1:
        return 0.0
    p_now = float(btc_closes.iloc[-1])
    p_old = float(btc_closes.iloc[-(window + 1)])
    return (p_now - p_old) / p_old if p_old > 0 else 0.0


def token_beta_vs_btc(token_closes: pd.Series, btc_closes: pd.Series,
                      window: int = 48) -> float:
    """Estimate token beta relative to BTC over recent `window` bars.

    beta > 1: token amplifies BTC moves (high leverage on BTC direction)
    beta ≈ 1: moves in line with BTC
    beta < 0: inverse (rare for majors)

    Returns 1.0 (neutral) when insufficient data.
    """
    n = min(len(token_closes), len(btc_closes), window + 1)
    if n < 10:
        return 1.0
    t_ret = token_closes.iloc[-n:].pct_change().dropna().values
    b_ret = btc_closes.iloc[-n:].pct_change().dropna().values
    m = min(len(t_ret), len(b_ret))
    if m < 5:
        return 1.0
    t_ret, b_ret = t_ret[-m:], b_ret[-m:]
    var_b = float(np.var(b_ret))
    if var_b < 1e-12:
        return 1.0
    cov = float(np.cov(t_ret, b_ret)[0][1])
    return float(np.clip(cov / var_b, -5.0, 10.0))   # cap extremes


# ── Bulk vectorised pre-computation ──────────────────────────────────────────

def precompute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorised pre-computation of all indicator columns across the whole
    DataFrame.  Call once per symbol before the bar loop; pass the enriched df
    to build_snapshot() and it will use the pre-computed columns instead of
    recomputing rolling windows per bar.

    Columns added:
        _rsi, _ema9, _ema21, _ema50, _macd_hist,
        _atr, _adx, _bb_upper, _bb_lower, _bb_pos, _bb_width_pct,
        _vwap, _vol_mean, _vol_std, _vol_ratio, _vol_zscore
    """
    closes  = df["close"]
    highs   = df["high"]
    lows    = df["low"]
    vol     = df["volume"]

    # ── RSI 14  (SMA method matching per-bar rsi() function) ─────────────
    # per-bar rsi() uses delta.clip().mean() over last (period+1)=15 diffs.
    # We replicate with rolling(15).mean() → identical at each bar.
    delta    = closes.diff()
    avg_gain = delta.clip(lower=0).rolling(15, min_periods=1).mean()
    avg_loss = (-delta.clip(upper=0)).rolling(15, min_periods=1).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-10)
    df["_rsi"] = (100 - 100 / (1 + rs)).fillna(50.0)

    # ── EMAs ──────────────────────────────────────────────────────────────
    df["_ema9"]  = closes.ewm(span=9,  adjust=False).mean()
    df["_ema21"] = closes.ewm(span=21, adjust=False).mean()
    df["_ema50"] = closes.ewm(span=50, adjust=False).mean()

    # ── MACD histogram ────────────────────────────────────────────────────
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    df["_macd_hist"] = (macd - macd.ewm(span=9, adjust=False).mean()).fillna(0.0)

    # ── ATR 14 ────────────────────────────────────────────────────────────
    tr = pd.concat([
        highs - lows,
        (highs - closes.shift()).abs(),
        (lows  - closes.shift()).abs(),
    ], axis=1).max(axis=1)
    df["_atr"] = tr.ewm(span=14, adjust=False).mean()

    # ── ADX 14 ────────────────────────────────────────────────────────────
    up_move    = highs.diff()
    down_move  = -lows.diff()
    plus_dm    = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm   = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    atr_s      = tr.ewm(span=14, adjust=False).mean()
    safe_atr   = atr_s.replace(0, 1e-9)
    plus_di    = 100 * plus_dm.ewm(span=14, adjust=False).mean()  / safe_atr
    minus_di   = 100 * minus_dm.ewm(span=14, adjust=False).mean() / safe_atr
    dsum       = (plus_di + minus_di).replace(0, 1e-9)
    dx         = 100 * (plus_di - minus_di).abs() / dsum
    df["_adx"] = dx.ewm(span=14, adjust=False).mean().fillna(20.0)

    # ── Bollinger Bands (20, 2) ────────────────────────────────────────────
    bb_sma     = closes.rolling(20, min_periods=1).mean()
    bb_std     = closes.rolling(20, min_periods=1).std().fillna(0.0)
    bb_upper   = bb_sma + 2 * bb_std
    bb_lower   = bb_sma - 2 * bb_std
    bb_width   = (bb_upper - bb_lower).replace(0, 1e-9)
    df["_bb_upper"]    = bb_upper
    df["_bb_lower"]    = bb_lower
    df["_bb_pos"]      = ((closes - bb_lower) / bb_width).fillna(0.5).clip(0, 1)
    df["_bb_width_pct"]= (bb_width / bb_sma.replace(0, 1e-9) * 100).fillna(0.0)

    # ── VWAP (rolling 20 bars) ────────────────────────────────────────────
    typical    = (highs + lows + closes) / 3
    tvol       = (typical * vol).rolling(20, min_periods=1).sum()
    vsum       = vol.rolling(20, min_periods=1).sum().replace(0, 1e-9)
    df["_vwap"]= (tvol / vsum).fillna(closes)

    # ── Volume stats (rolling 96 bars ≈ 8 h) ─────────────────────────────
    df["_vol_mean"]   = vol.rolling(96, min_periods=10).mean().fillna(vol)
    df["_vol_std"]    = vol.rolling(96, min_periods=10).std().fillna(0.0)
    df["_vol_ratio"]  = (vol / df["_vol_mean"].replace(0, 1e-9)).fillna(1.0)
    df["_vol_zscore"] = ((vol - df["_vol_mean"]) / df["_vol_std"].replace(0, 1)).fillna(0.0)

    return df


# ── Snapshot builder ──────────────────────────────────────────────────────────

def build_snapshot(df: pd.DataFrame, idx: int, symbol: str = "",
                   btc_df: pd.DataFrame | None = None,
                   hurst_override: float | None = None,
                   btc_row: int | None = None) -> dict:
    """
    Build a MathEngine-compatible snapshot dict from a rolling OHLCV window.
    Adapted from CryptoSentinelTrader backtest/engine.py build_snapshot().

    df             : full OHLCV DataFrame with columns [ts, open, high, low, close, volume]
    idx            : current bar index (inclusive)
    hurst_override : if provided, skip Hurst computation (use pre-cached value)
    btc_row        : pre-looked-up BTC row index (O(1) instead of O(N) ts scan per bar)
    """
    row     = df.iloc[idx]
    price   = float(row["close"])

    # Check if bulk indicators were pre-computed (fast path)
    _pre = "_rsi" in df.columns

    if _pre:
        # ── Fast path: look up pre-computed columns ────────────────────────
        rsi_val   = float(df["_rsi"].iat[idx])
        ema9_val  = float(df["_ema9"].iat[idx])
        ema21_val = float(df["_ema21"].iat[idx])
        ema50_val = float(df["_ema50"].iat[idx])
        macd_h    = float(df["_macd_hist"].iat[idx])
        atr_val   = float(df["_atr"].iat[idx])
        adx_val   = float(df["_adx"].iat[idx])
        bb_upper  = float(df["_bb_upper"].iat[idx])
        bb_lower  = float(df["_bb_lower"].iat[idx])
        bb_pos    = float(df["_bb_pos"].iat[idx])
        vol_ratio = float(df["_vol_ratio"].iat[idx])
        vol_z     = float(df["_vol_zscore"].iat[idx])
        vwap      = float(df["_vwap"].iat[idx])
    else:
        # ── Slow path: rolling window per-bar computation ─────────────────
        window  = df.iloc[max(0, idx - 59): idx + 1]
        closes  = window["close"]
        highs   = window["high"]
        lows    = window["low"]

        rsi_val    = rsi(closes)
        ema9_val   = ema(closes, 9)
        ema21_val  = ema(closes, 21)
        ema50_val  = ema(closes, 50)
        macd_h     = macd_hist(closes)
        atr_val    = atr(highs, lows, closes)
        adx_val    = adx(highs, lows, closes)
        bb_upper, bb_lower, bb_pos, _ = bollinger(closes)

        vol_now   = float(row["volume"])
        lookback  = df.iloc[max(0, idx - 96): idx]
        vol_mean  = float(lookback["volume"].mean()) if len(lookback) > 10 else vol_now
        vol_std   = float(lookback["volume"].std())  if len(lookback) > 10 else 0.0
        vol_ratio = vol_now / vol_mean if vol_mean > 0 else 1.0
        vol_z     = (vol_now - vol_mean) / vol_std if vol_std > 0 else 0.0

        vw_win    = df.iloc[max(0, idx - 19): idx + 1]
        typical   = (vw_win["high"] + vw_win["low"] + vw_win["close"]) / 3
        vol_sum   = vw_win["volume"].sum()
        vwap      = float((typical * vw_win["volume"]).sum() / vol_sum) if vol_sum > 0 else price

    # Trend (5m price change) — always computed from raw data
    o5m    = float(df.iloc[max(0, idx - 1)]["open"])
    ch5m   = (price - o5m) / o5m * 100 if o5m > 0 else 0.0
    trend  = "UP" if ch5m > 0.1 else "DOWN" if ch5m < -0.1 else "SIDEWAYS"
    trend_strength = min(abs(ch5m) / 1.0, 1.0)

    ema_align = (
        "BULLISH" if ema9_val > ema21_val > ema50_val else
        "BEARISH" if ema9_val < ema21_val < ema50_val else "MIXED"
    )

    vol_now   = float(row["volume"])
    vwap_dist = (price - vwap) / vwap * 100 if vwap > 0 else 0.0

    # Market regime
    regime = (
        "BREAKOUT"      if vol_ratio > 2.0 and abs(ch5m) > 0.5 else
        "TRENDING_UP"   if ch5m > 0.3 and adx_val > 25 else
        "TRENDING_DOWN" if ch5m < -0.3 and adx_val > 25 else "RANGING"
    )

    # Hurst (uses last 40 bars of closes); caller may pass cached value to avoid
    # recomputing every bar (regime changes on ~25min timescale, not 5min).
    if hurst_override is not None:
        hurst_val = hurst_override
    else:
        hurst_closes = df.iloc[max(0, idx - 39): idx + 1]["close"]
        hurst_val    = hurst_exponent(hurst_closes)

    # Token closes needed for beta calc (use last 50 bars regardless of fast/slow path)
    _tok_closes = df["close"].iloc[max(0, idx - 49): idx + 1]

    # BTC cross-asset: 4h return + token beta (requires btc_df aligned by ts)
    btc_4h_ret = 0.0
    beta_vs_btc = 1.0
    if btc_df is not None and len(btc_df) > 0:
        # btc_row is pre-looked-up by caller (O(1) dict); fallback to O(N) ts scan
        if btc_row is None:
            ts_now       = int(df.iloc[idx]["ts"])
            btc_row_mask = btc_df["ts"] == ts_now
            btc_idx_arr  = btc_df.index[btc_row_mask]
            btc_row = btc_idx_arr[0] if len(btc_idx_arr) > 0 else None
        if btc_row is not None:
            btc_i = btc_row
            btc_window   = btc_df.iloc[max(0, btc_i - 59): btc_i + 1]
            btc_closes_w = btc_window["close"]
            btc_4h_ret   = btc_4h_return(btc_closes_w, window=min(48, len(btc_closes_w) - 1))
            beta_vs_btc  = token_beta_vs_btc(_tok_closes, btc_closes_w,
                                              window=min(48, len(btc_closes_w) - 1))

    return {
        "symbol": symbol,
        "price": {
            "current":        price,
            "trend":          trend,
            "trend_strength": trend_strength,
            "change_5m_pct":  ch5m,
        },
        "volume": {
            "volume_ratio":   vol_ratio,
            "buy_sell_ratio": 0.55,   # neutral — no L2 order book in backtest
            "is_spike":       vol_ratio > 2.0,
            "volume_zscore_8h": round(vol_z, 2),
        },
        "indicators": {
            "rsi_14":          rsi_val,
            "ema_alignment":   ema_align,
            "macd_histogram":  macd_h,
            "atr_14":          atr_val,
            "adx":             adx_val,
            "vwap_distance_pct": vwap_dist,
            "bb_upper":        bb_upper,
            "bb_lower":        bb_lower,
            "bb_position":     bb_pos,
        },
        "market_regime": {"regime": regime},
        "meta": {
            "hurst":       hurst_val,
            "ema9":        ema9_val,
            "ema21":       ema21_val,
            "atr":         atr_val,
            "btc_4h_ret":  round(btc_4h_ret, 5),   # BTC 4h rolling return
            "beta_vs_btc": round(beta_vs_btc, 2),   # token beta vs BTC
        },
    }
