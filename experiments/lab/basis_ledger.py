"""A basis book kept in QUANTITIES, not in constant dollar weights.

basis.py marks `w · (spot_ret − perp_ret + funding)` every bar with w held
fixed between rebalances. That is a position whose dollar size is silently
reset to w every bar for free — a constant-dollar hedge — while what is
actually held is a fixed number of coins on both legs whose dollar size drifts
with price. The two differ in second order (review 2026-09-14, finding 3:
spot 100→200→100 vs perp 101→201→101 with zero funding earns +0.739% in the
old model and exactly 0 in a fixed-quantity hedge). On real 4h bars the gap is
tiny, but a ledger should not need that excuse.

Here each coin holds q units of spot and −q units of perp. Between rebalances
q is fixed; funding accrues on the perp notional q·P; a rebalance to target
dollar weights costs cost_per_side on |Δq| on BOTH legs; a change of target
(a config's own schedule or a walk-forward year boundary) is a real transition
that is charged, not spliced (finding 7).
"""
from __future__ import annotations

import numpy as np

import basis
import engine


def run(bp: basis.BasisPanel, w_target: np.ndarray,
        cost_per_side: float = engine.COST_PER_SIDE,
        equity0: float = 1.0) -> dict:
    """Per-bar equity returns of the quantity ledger following `w_target`.

    w_target[t] is the dollar-weight vector to hold from bar t on; the book
    re-targets only on bars where that vector differs from the previous one
    (or a held name loses data). Returns r (per-bar equity return) and the
    per-bar components in dollars: funding, basis drift, cost.
    """
    S, P, F = bp.spot, bp.perp, np.nan_to_num(bp.funding)
    n, m = S.shape
    q = np.zeros(m)
    equity = equity0
    r = np.zeros(n)
    fund_d = np.zeros(n)
    drift_d = np.zeros(n)
    cost_d = np.zeros(n)
    prev_w = None
    for t in range(n):
        ok = bp.mask[t]
        w = np.where(ok, np.nan_to_num(w_target[t]), 0.0)
        # mark the bar: fixed q earns basis drift and funding on the perp notional
        if t > 0:
            dS = np.where(ok & bp.mask[t - 1], S[t] - S[t - 1], 0.0)
            dP = np.where(ok & bp.mask[t - 1], P[t] - P[t - 1], 0.0)
            drift_d[t] = float(np.sum(q * (dS - dP)))
            fund_d[t] = float(np.sum(q * np.where(ok, P[t], 0.0) * F[t]))
            pnl = drift_d[t] + fund_d[t]
        else:
            pnl = 0.0
        # re-target when the weight vector changes (or a held name went dark)
        retarget = prev_w is None or not np.array_equal(w, prev_w) or bool(np.any((q != 0) & ~ok))
        if retarget:
            eq_pre = equity + pnl
            q_new = np.where(ok & (S[t] > 0), w * eq_pre / np.where(S[t] > 0, S[t], 1.0), 0.0)
            dq = np.abs(q_new - q)
            cost_d[t] = float(cost_per_side * np.sum(dq * np.where(ok, S[t], 0.0) + dq * np.where(ok, P[t], 0.0)))
            q = q_new
            prev_w = w
        pnl -= cost_d[t]
        r[t] = pnl / equity if equity > 0 else 0.0
        equity += pnl
    return {"r": r, "funding": fund_d, "drift": drift_d, "cost": cost_d}


def parts(led: dict, sl: slice, per_year: float, equity0: float = 1.0) -> dict:
    """Annualised %-of-initial-equity decomposition over a slice (like basis.parts)."""
    yrs = (sl.stop - sl.start) / per_year
    eq_path = equity0 * np.cumprod(1 + led["r"])
    base = float(eq_path[sl.start - 1]) if sl.start > 0 else equity0
    f = led["funding"][sl].sum() / base / yrs * 100
    d = led["drift"][sl].sum() / base / yrs * 100
    c = -led["cost"][sl].sum() / base / yrs * 100
    return {"funding": f, "basis_drift": d, "cost": c, "net": f + d + c}
