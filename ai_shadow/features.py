"""Deterministic, code-computed features — the model gets these next to the raw candles.

Plain arithmetic on closed candles only; no bot code is imported, so a change
in the strategy can never silently change what the reviewer sees.
"""
from __future__ import annotations

import math
from typing import Any

from .market import Candle

ROUND_TRIP_FEE = 0.0015   # 0.075% per side, the bot's EXEC_COST_PER_SIDE


def _r(x: float | None, nd: int = 4) -> float | None:
    if x is None or not math.isfinite(x):
        return None
    return round(x, nd)


def atr(candles: list[Candle], n: int = 14) -> float | None:
    if len(candles) < n + 1:
        return None
    trs = [max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
           for p, c in zip(candles[-n - 1:-1], candles[-n:])]
    return sum(trs) / n


def pct_change(candles: list[Candle], bars: int) -> float | None:
    if len(candles) <= bars or candles[-1 - bars].close == 0:
        return None
    return (candles[-1].close / candles[-1 - bars].close - 1) * 100


def realized_vol_pct(candles: list[Candle], n: int) -> float | None:
    if len(candles) < n + 1:
        return None
    rets = [math.log(c.close / p.close) for p, c in zip(candles[-n - 1:-1], candles[-n:])
            if p.close > 0 and c.close > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    return math.sqrt(sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)) * 100


def range_position(candles: list[Candle], n: int) -> float | None:
    """0 = at the n-bar low, 1 = at the n-bar high."""
    window = candles[-n:]
    if len(window) < n:
        return None
    lo, hi = min(c.low for c in window), max(c.high for c in window)
    return None if hi == lo else (candles[-1].close - lo) / (hi - lo)


def volume_ratio(candles: list[Candle], n: int) -> float | None:
    if len(candles) < n + 1:
        return None
    base = sum(c.volume for c in candles[-n - 1:-1]) / n
    return None if base == 0 else candles[-1].volume / base


def sma_gap_pct(candles: list[Candle], n: int) -> float | None:
    """How far the last close sits above (+) or below (−) its n-bar SMA."""
    if len(candles) < n:
        return None
    sma = sum(c.close for c in candles[-n:]) / n
    return None if sma == 0 else (candles[-1].close / sma - 1) * 100


def return_corr(a: list[Candle], b: list[Candle], n: int) -> float | None:
    """Correlation of per-bar returns over the last n bars, matched by open time."""
    bm = {c.open_ms: c.close for c in b}
    pairs = []
    for p, c in zip(a[-n - 1:-1], a[-n:]):
        if p.open_ms in bm and c.open_ms in bm and p.close and bm[p.open_ms]:
            pairs.append((c.close / p.close - 1, bm[c.open_ms] / bm[p.open_ms] - 1))
    if len(pairs) < max(10, n // 2):
        return None
    xs, ys = zip(*pairs)
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in pairs) / (sx * sy)


def timeframe_features(m5: list[Candle], h1: list[Candle], h4: list[Candle]) -> dict[str, Any]:
    last = m5[-1].close
    a5, a1, a4 = atr(m5), atr(h1), atr(h4)
    return {
        "last_close": last,
        "ret_pct": {"5m_x1": _r(pct_change(m5, 1)), "5m_x6": _r(pct_change(m5, 6)),
                    "5m_x12": _r(pct_change(m5, 12)), "5m_x48": _r(pct_change(m5, 48)),
                    "1h_x24": _r(pct_change(h1, 24)), "1h_x72": _r(pct_change(h1, 72)),
                    "4h_x42": _r(pct_change(h4, 42))},
        "atr14_pct": {"5m": _r(a5 / last * 100 if a5 else None),
                      "1h": _r(a1 / last * 100 if a1 else None),
                      "4h": _r(a4 / last * 100 if a4 else None)},
        "realized_vol_pct": {"5m_48": _r(realized_vol_pct(m5, 48)),
                             "1h_72": _r(realized_vol_pct(h1, 72))},
        "range_position": {"5m_48": _r(range_position(m5, 48), 3),
                           "1h_72": _r(range_position(h1, 72), 3),
                           "4h_90": _r(range_position(h4, 90), 3)},
        "volume_ratio_5m_last_vs_48": _r(volume_ratio(m5, 48), 3),
        "sma_gap_pct": {"1h_50": _r(sma_gap_pct(h1, 50)), "4h_50": _r(sma_gap_pct(h4, 50)),
                        "4h_200": _r(sma_gap_pct(h4, 200))},
    }


def geometry(direction: str, entry: float, sl: float | None, tp1: float | None,
             tp2: float | None, atr5: float | None) -> dict[str, Any]:
    """Stop and target distances as the bot set them at entry."""
    if not sl or entry <= 0:
        return {"available": False}
    sign = 1 if direction == "LONG" else -1
    risk = (entry - sl) * sign
    if risk <= 0:
        return {"available": False}
    sl_frac = risk / entry
    return {
        "available": True,
        "sl_pct": _r(sl_frac * 100),
        "sl_atr5": _r(risk / atr5 if atr5 else None, 2),
        "tp1_r": _r((tp1 - entry) * sign / risk if tp1 else None, 2),
        "tp2_r": _r((tp2 - entry) * sign / risk if tp2 else None, 2),
        # Round-trip fee as a share of the dollar risk: 0.0015 / sl_frac.
        "fee_share_of_risk": _r(ROUND_TRIP_FEE / sl_frac, 3),
    }
