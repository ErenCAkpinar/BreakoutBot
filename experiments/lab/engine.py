"""Portfolio evaluation: weights in, net return series out.

    r[t] = sum_s w[t-1, s] * ret[t, s]  -  cost * sum_s |w[t, s] - w[t-1, s]|

Two things are enforced HERE rather than trusted to the strategy author:

  the lag   A strategy sees bar t and its weights apply from t+1. Every
            lookahead bug this project could have is prevented by the engine
            shifting, instead of each strategy remembering to.

  the cost  Turnover is charged at the same 0.075% per side the live bot pays
            (Binance taker 0.05% + the 0.025% slippage measured from real order
            books). Today's forensic work found the published expectancy was
            three times better than the account because entry fees were counted
            nowhere; a lab that repeats that mistake is worse than no lab.

Weights are a fraction of equity, so a return series is directly comparable
across strategies with different trade counts and holding periods — which the
per-position R-multiples used elsewhere in this repo are not.
"""
from __future__ import annotations

import math

import numpy as np

COST_PER_SIDE = 0.00075     # matches config.EXEC_COST_PER_SIDE
BARS_PER_YEAR = {"5m": 105_120, "15m": 35_040, "30m": 17_520,
                 "1h": 8_760, "4h": 2_190, "1d": 365}


def evaluate(w: np.ndarray, ret: np.ndarray, mask: np.ndarray,
             cost: float = COST_PER_SIDE,
             funding: np.ndarray | None = None) -> np.ndarray:
    """Net per-bar portfolio return. `w` is the weight DECIDED on each bar.

    `funding` is the per-bar funding RATE, not a P&L. The sign convention is the
    exchange's: when the rate is positive, longs pay shorts. So a position earns
    `-weight * rate` — a short perp on a positive rate is paid to hold it. This
    is the first return component in this lab that does not come from price, and
    it is the reason the funding families exist at all.
    """
    w = np.nan_to_num(w, nan=0.0, posinf=0.0, neginf=0.0)
    w = np.where(mask, w, 0.0)              # never hold a coin that is not listed
    held = np.vstack([np.zeros((1, w.shape[1])), w[:-1]])   # the lag
    gross = np.nansum(held * np.nan_to_num(ret), axis=1)
    turn = np.abs(w - held).sum(axis=1)
    out = gross - cost * turn
    if funding is not None:
        out = out - np.nansum(held * np.nan_to_num(funding), axis=1)
    return out


def equity(r: np.ndarray) -> np.ndarray:
    """Compounded equity path from 1.0. Compounding, not summing: a strategy that
    loses 50% and gains 50% is down 25%, and only the geometric path says so."""
    return np.cumprod(1.0 + r)


def max_drawdown(eq: np.ndarray) -> float:
    peak = np.maximum.accumulate(eq)
    return float(np.min(eq / peak - 1.0)) if len(eq) else 0.0


def stats(r: np.ndarray, tf: str) -> dict:
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 30:
        return {"n": n, "sharpe": 0.0, "ann_ret": 0.0, "ann_vol": 0.0,
                "max_dd": 0.0, "total": 0.0, "hit": 0.0, "turnover": 0.0}
    per_year = BARS_PER_YEAR[tf]
    mu, sd = float(np.mean(r)), float(np.std(r))
    eq = equity(r)
    return {
        "n": n,
        # Annualised so timeframes are comparable — the whole point of testing
        # 15m against 1d is that they cannot be compared per-bar.
        "sharpe": (mu / sd * math.sqrt(per_year)) if sd > 1e-12 else 0.0,
        "ann_ret": float(eq[-1] ** (per_year / n) - 1.0) if eq[-1] > 0 else -1.0,
        "ann_vol": sd * math.sqrt(per_year),
        "max_dd": max_drawdown(eq),
        "total": float(eq[-1] - 1.0),
        "hit": float(np.mean(r > 0)),
    }


def bar_sharpe(r: np.ndarray) -> float:
    """Un-annualised Sharpe — the scale DSR/PBO expect.

    numpy rather than `statistics`: CSCV calls this once per config per partition
    per side, which is ~50k calls on a 357-config grid. The stdlib version made
    that the slowest step in the whole search by an order of magnitude.
    """
    a = np.asarray(r, dtype=float)
    a = a[np.isfinite(a)]
    if a.size < 3:
        return 0.0
    sd = float(a.std())
    return float(a.mean()) / sd if sd > 1e-12 else 0.0


def bar_sharpe_cols(R: np.ndarray) -> np.ndarray:
    """Sharpe of every column at once — what CSCV actually needs."""
    A = np.where(np.isfinite(R), R, np.nan)
    mu = np.nanmean(A, axis=0)
    sd = np.nanstd(A, axis=0)
    return np.divide(mu, sd, out=np.zeros_like(mu), where=sd > 1e-12)
