"""Strategy families, written without reference to the incumbent system.

Each returns a weight matrix w[t, s] — a fraction of equity per coin, decided on
bar t. The engine applies the lag and the cost, so nothing here has to remember
either.

WHY THESE SIX
-------------
The deployed system is one point in a much larger space: a single-asset,
5-minute, long-only breakout with a BTC regime gate. Every one of those four
choices was inherited. Today two of them were measured and found wanting — the
friction identity (cost/risk = 0.0015/sl_frac) punishes 5m stop distances, and
coin selection by past return carries no out-of-sample information.

So the families below vary the axes the incumbent never varied:

  cross-sectional vs time-series   Does the edge live in "which coin is strongest
                                   RIGHT NOW relative to the others" (xs_mom)
                                   or "is this coin trending at all" (ts_mom)?
                                   The incumbent can only express the second.
  momentum vs reversal             xs_rev is the sign-flipped hypothesis. If a
                                   short-horizon reversal pays where momentum
                                   does not, the whole thesis was backwards.
  signal vs no signal              inv_vol and buy_hold hold everything, weighted
                                   by risk or equally. They are the nulls: any
                                   signal-driven family has to beat "just be in
                                   the market", which no result in this repo has
                                   ever been checked against.
  the incumbent thesis, fairly     donchian is breakout in panel form, so the
                                   comparison is like-for-like rather than
                                   against a differently-instrumented system.

`rebal` throttles how often weights may change. It is the direct lever on
turnover, and turnover is what the friction arithmetic says actually matters.
"""
from __future__ import annotations

import numpy as np


# ── helpers ──────────────────────────────────────────────────────────────────

def _roll_ret(close: np.ndarray, lb: int) -> np.ndarray:
    """Trailing return over `lb` bars, ending at bar t (uses no future data)."""
    prev = np.vstack([np.full((lb, close.shape[1]), np.nan), close[:-lb]])
    with np.errstate(invalid="ignore", divide="ignore"):
        out = close / prev - 1.0
    return np.where(np.isfinite(out), out, np.nan)


def _roll_std(ret: np.ndarray, lb: int) -> np.ndarray:
    n, m = ret.shape
    out = np.full((n, m), np.nan)
    r = np.nan_to_num(ret)
    c1 = np.cumsum(r, axis=0)
    c2 = np.cumsum(r ** 2, axis=0)
    for t in range(lb, n):
        s1 = c1[t] - c1[t - lb]
        s2 = c2[t] - c2[t - lb]
        var = np.maximum(s2 / lb - (s1 / lb) ** 2, 0.0)
        out[t] = np.sqrt(var)
    return out


def _rolling_max(a: np.ndarray, lb: int) -> np.ndarray:
    """Max over the `lb` bars BEFORE t (t itself excluded — a breakout must be
    measured against a channel it did not help build)."""
    n, m = a.shape
    out = np.full((n, m), np.nan)
    for t in range(lb, n):
        out[t] = np.nanmax(a[t - lb:t], axis=0)
    return out


def _throttle(w: np.ndarray, rebal: int) -> np.ndarray:
    """Only let weights change every `rebal` bars; hold in between."""
    if rebal <= 1:
        return w
    out = w.copy()
    for t in range(1, w.shape[0]):
        if t % rebal:
            out[t] = out[t - 1]
    return out


def _normalise(w: np.ndarray, gross: float = 1.0) -> np.ndarray:
    """Scale each row so total absolute exposure is `gross`. Without this, a
    strategy holding 10 coins is silently 10x levered against one holding 1, and
    the comparison measures position count instead of skill."""
    s = np.abs(w).sum(axis=1, keepdims=True)
    return np.divide(w, s, out=np.zeros_like(w), where=s > 1e-12) * gross


# ── families ─────────────────────────────────────────────────────────────────

def buy_hold(p, **_) -> np.ndarray:
    """Equal-weight, always in. The null every signal must beat."""
    w = np.where(p.mask, 1.0, 0.0)
    return _normalise(w)


def inv_vol(p, lookback: int = 100, rebal: int = 1, **_) -> np.ndarray:
    """Always in, weighted by 1/volatility. The second null: it asks whether an
    edge is a signal or just risk-scaling."""
    vol = _roll_std(p.ret, lookback)
    w = np.where(p.mask & np.isfinite(vol) & (vol > 1e-9), 1.0 / (vol + 1e-9), 0.0)
    return _normalise(_throttle(w, rebal))


def ts_mom(p, lookback: int = 100, rebal: int = 1, **_) -> np.ndarray:
    """Long every coin whose trailing return is positive; flat otherwise."""
    m = _roll_ret(p.close, lookback)
    w = np.where(p.mask & (m > 0) & np.isfinite(m), 1.0, 0.0)
    return _normalise(_throttle(w, rebal))


def xs_mom(p, lookback: int = 100, k: int = 5, longshort: bool = False,
           rebal: int = 1, **_) -> np.ndarray:
    """Long the top-k by trailing return; optionally short the bottom-k.

    Cross-sectional: it does not care whether the market is up, only which coins
    lead it. That is the axis the incumbent's per-symbol state machine cannot
    express at all.
    """
    m = _roll_ret(p.close, lookback)
    n, s = m.shape
    w = np.zeros((n, s))
    for t in range(n):
        row = m[t]
        ok = np.where(np.isfinite(row) & p.mask[t])[0]
        if len(ok) < 2 * k if longshort else len(ok) < k:
            continue
        order = ok[np.argsort(row[ok])]
        w[t, order[-k:]] = 1.0
        if longshort:
            w[t, order[:k]] = -1.0
    return _normalise(_throttle(w, rebal))


def xs_rev(p, lookback: int = 12, k: int = 5, rebal: int = 1, **_) -> np.ndarray:
    """Long the WORST performers over a short lookback — momentum's sign-flip."""
    m = _roll_ret(p.close, lookback)
    n, s = m.shape
    w = np.zeros((n, s))
    for t in range(n):
        row = m[t]
        ok = np.where(np.isfinite(row) & p.mask[t])[0]
        if len(ok) < k:
            continue
        order = ok[np.argsort(row[ok])]
        w[t, order[:k]] = 1.0
    return _normalise(_throttle(w, rebal))


def donchian(p, lookback: int = 100, rebal: int = 1, **_) -> np.ndarray:
    """Long while price sits above the prior `lookback`-bar high — the incumbent's
    thesis, expressed on the same footing as everything else."""
    hi = _rolling_max(p.high, lookback)
    brk = p.close > hi
    n, s = brk.shape
    w = np.zeros((n, s))
    state = np.zeros(s, dtype=bool)
    lo = _rolling_max(-p.low, lookback)          # = -(rolling min)
    for t in range(n):
        state = np.where(brk[t], True, state)
        state = np.where(p.close[t] < -lo[t], False, state)   # exit on lower band
        w[t] = np.where(state & p.mask[t], 1.0, 0.0)
    return _normalise(_throttle(w, rebal))


FAMILIES = {
    "buy_hold": buy_hold,
    "inv_vol": inv_vol,
    "ts_mom": ts_mom,
    "xs_mom": xs_mom,
    "xs_rev": xs_rev,
    "donchian": donchian,
}
