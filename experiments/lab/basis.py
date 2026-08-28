"""The real carry trade: long spot + short perp on the SAME asset.

    python3.12 experiments/lab/basis.py [--tf 4h] [--top 5] [--rebal 42]

WHAT MAKES THIS DIFFERENT FROM EVERYTHING ELSE IN THE LAB
---------------------------------------------------------
Every other family here takes a view. This one does not. Holding a coin's spot
and shorting its own perpetual leaves no meaningful exposure to that coin's
price — the two legs move together — so the position is not a bet on direction,
it is a claim on a cash flow. Per bar, per unit of notional:

    r = spot_return - perp_return + funding_rate

The first two terms cancel almost exactly. What is left is the funding the short
leg is paid, plus the small drift of the basis between the two prices.

The funding study established the income is real: positive in 6 of 6
sub-periods across 5.8 years, +4.7%/yr on average. It also showed why the
cross-sectional version wasted it — that construction shorted one coin's perp
against a DIFFERENT coin's perp, so it carried the relative price move between
two unrelated assets and ended at -0.4%/yr with 34.5% vol. Here that risk is
removed by construction rather than diversified away.

COSTS ARE DOUBLE, AND THAT IS THE WHOLE DESIGN PROBLEM
------------------------------------------------------
Opening a basis position is TWO trades (buy spot, short perp) and closing it is
two more. Soeach unit of weight change costs 2 x cost_per_side, not one. Against a
median funding of ~4%/yr recently, a full rotation costs ~0.3% — which is three
weeks of income. Rotating weekly would spend more than the trade earns. The
rebalance interval is therefore not a tuning knob here, it is the strategy.
"""
from __future__ import annotations

import argparse
import glob
import itertools
import json
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
os.chdir(_ROOT)

import engine  # noqa: E402
import panel as panel_mod  # noqa: E402

SPOT = "experiments/lab/spot"
FUND = "experiments/lab/funding"


class BasisPanel:
    """Aligned spot close, perp close and per-bar funding for one timeframe."""

    def __init__(self, symbols, index, spot, perp, funding):
        self.symbols, self.index = symbols, index
        self.spot, self.perp, self.funding = spot, perp, funding
        self.mask = np.isfinite(spot) & np.isfinite(perp)

        def rets(a):
            prev = np.vstack([np.full((1, a.shape[1]), np.nan), a[:-1]])
            with np.errstate(invalid="ignore", divide="ignore"):
                r = a / prev - 1.0
            return np.where(np.isfinite(r), r, 0.0)

        self.spot_ret, self.perp_ret = rets(spot), rets(perp)
        # The carry return of one unit of basis position, before costs.
        self.carry = self.spot_ret - self.perp_ret + np.nan_to_num(funding)

    def __repr__(self):
        return (f"BasisPanel({len(self.symbols)} sym x {len(self.index)} bar, "
                f"{self.index[0]:%Y-%m-%d}→{self.index[-1]:%Y-%m-%d})")


def load(tf: str = "4h", days: int | None = None) -> BasisPanel:
    syms = sorted({os.path.basename(p).rsplit("_", 1)[0]
                   for p in glob.glob(f"{SPOT}/*_{tf}.json")})
    if not syms:
        raise SystemExit(f"{SPOT}/ boş — önce fetch_spot.py koş")

    spot_s, perp_s = {}, {}
    for s in syms:
        d = json.load(open(f"{SPOT}/{s}_{tf}.json"))
        ts = pd.to_datetime([b[0] for b in d["bars"]], unit="ms", utc=True)
        spot_s[s] = pd.Series([b[1] for b in d["bars"]], index=ts)
        # Perp closes come from the 5m cache, resampled to the same grid.
        for dd in (2095, 665):
            # Self-generated OHLCV written by this repo's own fetch; nothing
            # external is ever unpickled (same convention as curate.py).
            p = panel_mod._cache_path(s, dd)
            if os.path.exists(p):
                perp_s[s] = panel_mod.resample(pd.read_pickle(p), tf)["close"]
                break

    syms = [s for s in syms if s in perp_s]
    idx = None
    for s in syms:
        i = spot_s[s].index.intersection(perp_s[s].index)
        idx = i if idx is None else idx.union(i)
    idx = idx.sort_values()
    if days:
        idx = idx[idx >= idx[-1] - pd.Timedelta(days=days)]

    spot = np.column_stack([spot_s[s].reindex(idx).to_numpy(float) for s in syms])
    perp = np.column_stack([perp_s[s].reindex(idx).to_numpy(float) for s in syms])
    fund = panel_mod.load_funding(syms, idx, tf)
    if fund is None:
        fund = np.zeros_like(spot)
    return BasisPanel(syms, idx, spot, perp, fund)


def _roll_mean(a: np.ndarray, lb: int) -> np.ndarray:
    n, m = a.shape
    out = np.full((n, m), np.nan)
    c = np.cumsum(np.nan_to_num(a), axis=0)
    out[lb:] = (c[lb:] - c[:-lb]) / lb
    return out


def weights(bp: BasisPanel, top: int, lookback: int, rebal: int,
            min_funding: float = 0.0) -> np.ndarray:
    """Hold the `top` coins by trailing funding, equal weight, long-basis only.

    `min_funding` refuses a coin whose trailing rate is below the threshold: a
    basis position on negative funding PAYS to exist, and 5 of 23 coins ran
    negative over the last two years. Selection here is not a forecast — it is
    reading a posted rate.
    """
    f = _roll_mean(bp.funding, lookback)
    n, s = f.shape
    w = np.zeros((n, s))
    for t in range(0, n, max(1, rebal)):
        row = f[t]
        ok = np.where(np.isfinite(row) & bp.mask[t] & (row > min_funding))[0]
        if len(ok) == 0:
            continue
        chosen = ok[np.argsort(row[ok])][-top:]
        w[t, chosen] = 1.0 / len(chosen)
    # hold between rebalances
    for t in range(1, n):
        if t % max(1, rebal):
            w[t] = w[t - 1]
    return w


def evaluate(bp: BasisPanel, w: np.ndarray,
             cost_per_side: float = engine.COST_PER_SIDE) -> np.ndarray:
    """Per-bar return. Cost is doubled: every weight change moves TWO legs."""
    w = np.where(bp.mask, np.nan_to_num(w), 0.0)
    held = np.vstack([np.zeros((1, w.shape[1])), w[:-1]])
    gross = np.nansum(held * bp.carry, axis=1)
    turn = np.abs(w - held).sum(axis=1)
    return gross - 2.0 * cost_per_side * turn


def parts(bp: BasisPanel, w: np.ndarray, sl: slice, per_year: float,
          cost_per_side: float = engine.COST_PER_SIDE) -> dict:
    w = np.where(bp.mask, np.nan_to_num(w), 0.0)
    held = np.vstack([np.zeros((1, w.shape[1])), w[:-1]])
    yrs = (sl.stop - sl.start) / per_year
    px = ((held * (bp.spot_ret - bp.perp_ret))[sl].sum()) / yrs * 100
    fu = ((held * np.nan_to_num(bp.funding))[sl].sum()) / yrs * 100
    co = -2.0 * cost_per_side * np.abs(w - held)[sl].sum() / yrs * 100
    return {"basis_drift": px, "funding": fu, "cost": co, "net": px + fu + co}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--days", type=int, default=0)
    args = ap.parse_args()

    bp = load(args.tf, args.days or None)
    per_year = engine.BARS_PER_YEAR[args.tf]
    n = len(bp.index)
    print(f"\n  {bp}\n  {len(bp.symbols)} coin · spot+perp+funding hizalı\n")

    print("  IZGARA — hepsi tek varlıkta delta-nötr (yön riski yapısal olarak yok)")
    print(f"  {'top':>4} {'lb':>5} {'rebal':>6} {'minF':>7} | {'basis':>8} {'funding':>8} "
          f"{'maliyet':>8} {'NET':>8} {'vol':>7} {'DD':>8} {'SR':>7}")
    rows = []
    for top, lb, reb, mf in itertools.product([3, 5, 8], [42, 90, 180],
                                              [42, 180, 540], [0.0, 0.00005]):
        w = weights(bp, top, lb, reb, mf)
        r = evaluate(bp, w)
        st = engine.stats(r, args.tf)
        pt = parts(bp, w, slice(0, n), per_year)
        rows.append({**pt, "top": top, "lb": lb, "reb": reb, "mf": mf,
                     "sr": st["sharpe"], "dd": st["max_dd"],
                     "vol": st["ann_vol"], "ann": st["ann_ret"], "_r": r})
    for x in sorted(rows, key=lambda r: -r["net"])[:14]:
        print(f"  {x['top']:>4} {x['lb']:>5} {x['reb']:>6} {x['mf']*100:>6.3f}% | "
              f"{x['basis_drift']:>+7.1f}% {x['funding']:>+7.1f}% {x['cost']:>+7.1f}% "
              f"{x['net']:>+7.1f}% {x['vol']*100:>6.1f}% {x['dd']*100:>7.1f}% {x['sr']:>+7.2f}")

    best = max(rows, key=lambda r: r["sr"])
    print(f"\n  EN İYİ SHARPE: top={best['top']} lb={best['lb']} rebal={best['reb']} "
          f"minF={best['mf']*100:.3f}%")
    print(f"    yıllık {best['ann']*100:+.2f}%  vol {best['vol']*100:.2f}%  "
          f"SR {best['sr']:+.2f}  DD {best['dd']*100:.2f}%")
    print(f"    ayrıştırma: basis {best['basis_drift']:+.1f}%  "
          f"funding {best['funding']:+.1f}%  maliyet {best['cost']:+.1f}%")

    # Sub-period stability — an income stream has to show up in every era.
    print("\n  ALT-DÖNEM İSTİKRARI (en iyi Sharpe konfigi)")
    K = 6
    edges = [int(n * i / K) for i in range(K + 1)]
    for i in range(K):
        sl = slice(edges[i], edges[i + 1])
        pt = parts(bp, weights(bp, best["top"], best["lb"], best["reb"], best["mf"]),
                   sl, per_year)
        a, b = bp.index[edges[i]], bp.index[edges[i + 1] - 1]
        print(f"    {a:%Y-%m}→{b:%Y-%m}  funding {pt['funding']:>+6.1f}%  "
              f"basis {pt['basis_drift']:>+6.1f}%  maliyet {pt['cost']:>+5.1f}%  "
              f"NET {pt['net']:>+6.1f}%")

    print("\n  MALİYET DUYARLILIĞI (en iyi konfig)")
    for c, lab in [(0.00075, "%0.075 taker"), (0.0004, "%0.040"),
                   (0.0002, "%0.020 maker"), (0.0001, "%0.010")]:
        w = weights(bp, best["top"], best["lb"], best["reb"], best["mf"])
        st = engine.stats(evaluate(bp, w, c), args.tf)
        print(f"    {lab:<16} yıllık {st['ann_ret']*100:+6.2f}%  SR {st['sharpe']:+.2f}")
    print()


if __name__ == "__main__":
    main()
