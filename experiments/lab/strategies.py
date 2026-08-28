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


def _score(p, lookback: int, skip: int = 0, vol_adj: bool = False) -> np.ndarray:
    """The ranking signal, with two documented refinements available.

    skip     rank on the return from t-lookback to t-skip, ignoring the most
             recent `skip` bars. Standard in the equities momentum literature
             because the newest move is contaminated by short-horizon reversal —
             and this repo's own xs_rev family exists precisely because that
             reversal is real here too.
    vol_adj  divide by trailing volatility, so the ranking compares risk-adjusted
             moves rather than rewarding whichever coin is simply the wildest.
    """
    if skip <= 0:
        m = _roll_ret(p.close, lookback)
    else:
        past = np.vstack([np.full((lookback, p.close.shape[1]), np.nan),
                          p.close[:-lookback]])
        recent = np.vstack([np.full((skip, p.close.shape[1]), np.nan),
                            p.close[:-skip]])
        with np.errstate(invalid="ignore", divide="ignore"):
            m = recent / past - 1.0
        m = np.where(np.isfinite(m), m, np.nan)
    if vol_adj:
        v = _roll_std(p.ret, max(lookback, 20))
        m = np.divide(m, v, out=np.full_like(m, np.nan), where=v > 1e-9)
    return m


def xs_mom(p, lookback: int = 100, k: int = 5, longshort: bool = False,
           rebal: int = 1, skip: int = 0, vol_adj: bool = False,
           buffer: int = 0, **_) -> np.ndarray:
    """Long the top-k by trailing return; optionally short the bottom-k.

    Cross-sectional: it does not care whether the market is up, only which coins
    lead it. That is the axis the incumbent's per-symbol state machine cannot
    express at all.

    `buffer` adds hysteresis: a name entering the book must rank in the top k,
    but only leaves once it falls out of the top k+buffer. Measured on the 665d
    test period, the gross cross-sectional spread was +1.6% while turnover cost
    -2.5% — the edge is real and smaller than the friction, so what the book
    does at the RANKING BOUNDARY is the whole game. Without hysteresis a coin
    oscillating around rank k is bought and sold repeatedly for nothing.
    """
    m = _score(p, lookback, skip, vol_adj)
    n, s = m.shape
    w = np.zeros((n, s))
    held_l: set[int] = set()
    held_s: set[int] = set()
    for t in range(n):
        row = m[t]
        ok = np.where(np.isfinite(row) & p.mask[t])[0]
        need = 2 * k if longshort else k
        if len(ok) < need:
            continue
        order = ok[np.argsort(row[ok])]          # worst → best
        best_first = list(reversed(order))       # best → worst

        def pick(ranked: list, held: set[int]) -> set[int]:
            """Keep what is still inside the wider band, refill from the top.

            With buffer=0 the band equals the target set and this reduces to a
            plain top-k, so the two paths cannot drift apart.
            """
            band = set(int(i) for i in ranked[:k + buffer])
            out = {i for i in held if i in band}
            for i in ranked:
                if len(out) >= k:
                    break
                out.add(int(i))
            return out

        longs = pick(best_first, held_l)
        shorts = pick(list(order), held_s) - longs if longshort else set()
        held_l, held_s = longs, shorts
        for i in longs:
            w[t, i] = 1.0
        for i in shorts:
            w[t, i] = -1.0
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
