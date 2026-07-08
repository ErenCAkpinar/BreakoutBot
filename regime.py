"""
Regime classifier (FAZ 1) — directional market-regime detection.

Replaces the crude "BTC 4h return < -1.5%" hair-trigger gate with a structural
regime based on a 200-period 200-MA + slope on 4h candles, blended per coin:

    regime = BTC_WEIGHT × btc_score  +  (1-BTC_WEIGHT) × coin_score

Each asset score ∈ {+1 bull, 0 neutral, -1 bear}:
    BULL  : price > 200MA(4h) AND 200MA rising
    BEAR  : price < 200MA(4h) AND 200MA falling
    NEUT  : otherwise

Blended score thresholded:  ≥ +0.40 → BULL,  ≤ -0.40 → BEAR,  else NEUTRAL.

Kept deliberately to ≤5 parameters (overfit defense per research).
Used by both backtest (resample 5m→4h) and live (fetch 4h directly).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ── Parameters (≤5 — keep it that way) ────────────────────────────────────────
MA_PERIOD      = 200     # 200-period MA on 4h  (~33 days)
SLOPE_LOOKBACK = 10      # bars back to measure 200-MA slope (~40h)
BTC_WEIGHT     = 0.575   # BTC macro weight in the blend (Eren: 55-60%)
BULL_THRESHOLD = 0.40    # blended ≥ this → BULL
BEAR_THRESHOLD = -0.40   # blended ≤ this → BEAR


def resample_4h(df_5m: pd.DataFrame) -> pd.DataFrame:
    """Resample 5m OHLCV (cols ts/open/high/low/close/volume, ts in ms) to 4h."""
    d = df_5m.copy()
    d["_dt"] = pd.to_datetime(d["ts"], unit="ms", utc=True)
    d = d.set_index("_dt")
    out = pd.DataFrame({
        "open":   d["open"].resample("4h").first(),
        "high":   d["high"].resample("4h").max(),
        "low":    d["low"].resample("4h").min(),
        "close":  d["close"].resample("4h").last(),
        "volume": d["volume"].resample("4h").sum(),
    }).dropna()
    return out


def asset_score(df_4h: pd.DataFrame) -> float:
    """Point-in-time regime score for ONE asset from its 4h frame (live use).

    Returns +1.0 (bull) / 0.0 (neutral) / -1.0 (bear) using the LAST closed bar.
    """
    if len(df_4h) < MA_PERIOD + SLOPE_LOOKBACK:
        return 0.0
    ma      = df_4h["close"].rolling(MA_PERIOD).mean()
    price   = float(df_4h["close"].iloc[-1])
    ma_now  = float(ma.iloc[-1])
    ma_prev = float(ma.iloc[-1 - SLOPE_LOOKBACK])
    if ma_now <= 0 or pd.isna(ma_now) or pd.isna(ma_prev):
        return 0.0
    above  = price > ma_now
    rising = (ma_now - ma_prev) > 0
    if above and rising:
        return 1.0
    if (not above) and (not rising):
        return -1.0
    return 0.0


def score_series_4h(df_4h: pd.DataFrame) -> pd.Series:
    """Vectorised per-4h-bar regime score (+1/0/-1), NO lookahead (backtest use).

    Indexed by the 4h frame's index so callers can forward-fill onto 5m bars.
    """
    ma       = df_4h["close"].rolling(MA_PERIOD).mean()
    ma_prev  = ma.shift(SLOPE_LOOKBACK)
    above    = df_4h["close"] > ma
    rising   = (ma - ma_prev) > 0
    score    = pd.Series(0.0, index=df_4h.index)
    score[above & rising]          = 1.0
    score[(~above) & (~rising)]    = -1.0
    score[ma.isna() | ma_prev.isna()] = 0.0
    return score


def classify(btc_score: float, coin_score: float) -> str:
    """Blend BTC + coin scores → 'BULL' | 'NEUTRAL' | 'BEAR'."""
    blended = BTC_WEIGHT * btc_score + (1.0 - BTC_WEIGHT) * coin_score
    if blended >= BULL_THRESHOLD:
        return "BULL"
    if blended <= BEAR_THRESHOLD:
        return "BEAR"
    return "NEUTRAL"


# ── Behaviour map (FAZ 1: long throttling only; short comes in FAZ 3) ─────────
# size multiplier applied to RISK_PER_TRADE_USD for LONG entries per regime.
import os
# FAZ 4c (baked in): momentum longs ONLY in BULL. Validated best in BOTH windows —
#   90d 23-coin: −$152 (0.35/0.35) → −$75 (BULL-only)
#   240d bear:   −$37  → −$20, MaxDD −20.5% → −15.3% (avoids hard stop)
# Rationale: breakout-momentum needs a real uptrend; in NEUTRAL chop the MR sleeve
# takes over, in BEAR nothing trades. (env vars kept for future sweeps.)
LONG_SIZE_MULT = {
    "BULL":    float(os.getenv("LM_BULL",    "1.00")),   # full risk (trend = edge)
    "NEUTRAL": float(os.getenv("LM_NEUTRAL", "0.00")),   # no longs in chop → MR handles it
    "BEAR":    float(os.getenv("LM_BEAR",    "0.00")),   # no longs in downtrend (defense)
}


def current_regime(coin_4h: pd.DataFrame, btc_4h: pd.DataFrame) -> str:
    """Convenience: point-in-time blended regime for live use."""
    return classify(asset_score(btc_4h), asset_score(coin_4h))


def backtest_regimes(df_coin_5m: pd.DataFrame,
                     df_btc_5m: "pd.DataFrame | None") -> np.ndarray:
    """Per-5m-bar regime label ('BULL'/'NEUTRAL'/'BEAR'), lookahead-safe.

    Each 5m bar uses the latest 4h regime that has ALREADY CLOSED (4h scores
    shifted +4h before mapping). Returns an object ndarray aligned to the rows
    of df_coin_5m. Used by the backtest to throttle long size per regime.
    """
    n = len(df_coin_5m)

    def _scores(df5: pd.DataFrame, name: str) -> pd.DataFrame:
        sc  = score_series_4h(resample_4h(df5))       # Series indexed by 4h start
        out = sc.reset_index()
        out.columns = ["dt", name]
        out["dt"]   = (out["dt"] + pd.Timedelta("4h")).dt.as_unit("ns")  # after close
        return out.sort_values("dt").reset_index(drop=True)

    base = pd.DataFrame({"dt": pd.to_datetime(df_coin_5m["ts"].values,
                                              unit="ms", utc=True)})
    base["dt"]   = base["dt"].dt.as_unit("ns")   # match merge-key resolution
    base["_row"] = np.arange(n)
    base = base.sort_values("dt").reset_index(drop=True)

    base = pd.merge_asof(base, _scores(df_coin_5m, "coin_s"),
                         on="dt", direction="backward")
    if df_btc_5m is not None:
        base = pd.merge_asof(base, _scores(df_btc_5m, "btc_s"),
                             on="dt", direction="backward")
    else:
        base["btc_s"] = 0.0

    coin_s  = base["coin_s"].fillna(0.0).to_numpy()
    btc_s   = base["btc_s"].fillna(0.0).to_numpy()
    blended = BTC_WEIGHT * btc_s + (1.0 - BTC_WEIGHT) * coin_s
    labels  = np.where(blended >= BULL_THRESHOLD, "BULL",
              np.where(blended <= BEAR_THRESHOLD, "BEAR", "NEUTRAL"))

    out = np.empty(n, dtype=object)
    out[base["_row"].to_numpy()] = labels
    return out
