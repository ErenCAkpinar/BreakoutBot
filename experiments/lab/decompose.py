"""Is the long/short book earning alpha, or just not being in the market?

    python3.12 experiments/lab/decompose.py --days 665 --tf 4h

WHY THIS COMES BEFORE ANY REFINEMENT
------------------------------------
The search's headline read: while the alt market fell 40% with a 54% drawdown,
the best long/short cross-sectional momentum arm lost 2.3% with a 10.5%
drawdown. That looks like skill. It may be nothing of the kind.

`_normalise` scales GROSS exposure to 1.0, so a long/short book holds roughly
+0.5 long and -0.5 short and its NET exposure is ~0 by construction. A book with
no market exposure cannot lose to a falling market — that is arithmetic, not
edge. Praising it for surviving the bear would be praising it for being flat.

So this separates the two:

  net exposure   If it is ~0, "survived the drawdown" carries no information and
                 the only number that matters is the spread.
  leg attribution  long-leg return vs short-leg return. A market-neutral book
                 earns (long - short); if that is negative the strategy is
                 picking the wrong side, whatever the headline says.
  vol-matched null  buy_hold scaled down to the SAME realised volatility. If the
                 L/S book merely has less risk, the scaled market matches it and
                 there is no skill to find.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
os.chdir(_ROOT)

import engine  # noqa: E402
import panel as panel_mod  # noqa: E402
import strategies as S  # noqa: E402


def leg_returns(w: np.ndarray, ret: np.ndarray, mask: np.ndarray,
                cost: float = engine.COST_PER_SIDE):
    """Split the net series into its long and short halves, costs included."""
    w = np.where(mask, np.nan_to_num(w), 0.0)
    held = np.vstack([np.zeros((1, w.shape[1])), w[:-1]])
    r = np.nan_to_num(ret)
    longs, shorts = np.clip(held, 0, None), np.clip(held, None, 0)
    turn = np.abs(w - held).sum(axis=1)
    return {
        "long": (longs * r).sum(axis=1),
        "short": (shorts * r).sum(axis=1),
        "cost": -cost * turn,
        "net_exposure": held.sum(axis=1),
        "gross_exposure": np.abs(held).sum(axis=1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=665)
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--split", type=float, default=0.6)
    args = ap.parse_args()

    syms = panel_mod.available(args.days)
    p = panel_mod.load(syms, args.days, args.tf)
    cut = int(p.shape[0] * args.split)
    print(f"\n  {p}  ·  test = son {p.shape[0]-cut} bar\n")

    arms = [
        ("uzun/kısa k=8 lb=200", dict(lookback=200, k=8, longshort=True, rebal=24)),
        ("uzun/kısa k=5 lb=200", dict(lookback=200, k=5, longshort=True, rebal=6)),
        ("SADECE UZUN k=8 lb=200", dict(lookback=200, k=8, longshort=False, rebal=24)),
    ]

    print(f"  {'kol':<24} {'net maruz.':>11} {'gross':>7} "
          f"{'uzun bacak':>11} {'kısa bacak':>11} {'maliyet':>9} {'NET':>9}")
    for name, kw in arms:
        w = S.xs_mom(p, **kw)
        d = leg_returns(w, p.ret, p.mask)
        sl = slice(cut, None)
        tot = d["long"][sl] + d["short"][sl] + d["cost"][sl]
        print(f"  {name:<24} {d['net_exposure'][sl].mean():>11.3f} "
              f"{d['gross_exposure'][sl].mean():>7.2f} "
              f"{d['long'][sl].sum()*100:>10.1f}% {d['short'][sl].sum()*100:>10.1f}% "
              f"{d['cost'][sl].sum()*100:>8.1f}% {tot.sum()*100:>8.1f}%")

    # The null that actually matters: the market, scaled to the same risk.
    print()
    bh = engine.evaluate(S.buy_hold(p), p.ret, p.mask)[cut:]
    ls = engine.evaluate(S.xs_mom(p, **arms[0][1]), p.ret, p.mask)[cut:]
    k = np.std(ls) / np.std(bh) if np.std(bh) > 0 else 0.0
    scaled = bh * k
    per_year = engine.BARS_PER_YEAR[args.tf]

    def line(tag: str, r: np.ndarray) -> None:
        st = engine.stats(r, args.tf)
        print(f"  {tag:<34} yıllık {st['ann_ret']*100:>7.1f}%   "
              f"vol {st['ann_vol']*100:>5.1f}%   SR {st['sharpe']:>+5.2f}   "
              f"DD {st['max_dd']*100:>6.1f}%")

    print("  VOLATİLİTE EŞİTLENMİŞ KARŞILAŞTIRMA (test dönemi)")
    line("uzun/kısa kol", ls)
    line(f"buy_hold × {k:.3f} (aynı vol)", scaled)
    line("buy_hold (ölçeklenmemiş)", bh)

    # Beta of the L/S book on the market: how neutral is it really?
    mkt = bh
    if np.std(mkt) > 1e-12:
        beta = float(np.cov(ls, mkt)[0, 1] / np.var(mkt))
        alpha = float(np.mean(ls) - beta * np.mean(mkt)) * per_year
        print(f"\n  piyasaya beta {beta:+.3f}   →   yıllık alfa {alpha*100:+.1f}%")
        print("  (beta ~0 ise 'düşüşte ayakta kaldı' bilgi taşımaz; hüküm alfada)")
    print()


if __name__ == "__main__":
    main()
