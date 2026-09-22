"""Focused study of the market-neutral cross-sectional book.

    python3.12 experiments/lab/ls_study.py --days 665 --tf 4h

WHAT THE BROAD SEARCH LEFT
--------------------------
Decomposition of the best long/short arm over the 665d test period:

    long leg  -15.7%   short leg  +17.3%   →  gross spread  +1.6%
    turnover cost                             -2.5%
    net                                       -0.9%

Net exposure 0.000, beta -0.021: the book really is market neutral, so its
survival through a 40% market decline is arithmetic rather than skill. The only
number that carries information is the spread — and the spread is REAL but
SMALLER THAN THE FRICTION. That is the same finding as the friction identity
(cost/risk = 0.0015/sl_frac) reached from the other direction.

So this grid does not chase Sharpe. It reports gross spread, cost and net as
three separate columns, because the question is no longer "which config wins"
but "can the spread be widened or the cost cut until one exceeds the other".
Levers: rebalance interval, hysteresis at the ranking boundary, skipping the
most recent bars, and risk-adjusting the ranking.

Every configuration is counted and the count is carried into the DSR, as in
search.py — a focused grid is still a grid.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import statistics
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, "experiments"))
os.chdir(_ROOT)

import engine  # noqa: E402
import panel as panel_mod  # noqa: E402
import strategies as S  # noqa: E402
import wfa  # noqa: E402
from decompose import leg_returns  # noqa: E402

OUT = "experiments/lab/results"

GRID = {
    "lookback": [100, 200, 400],
    "k": [3, 5, 8],
    "rebal": [6, 24, 48, 96],
    "buffer": [0, 3, 6],
    "skip": [0, 6],
    "vol_adj": [False, True],
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=665)
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--split", type=float, default=0.6)
    args = ap.parse_args()

    syms = panel_mod.available(args.days)
    p = panel_mod.load(syms, args.days, args.tf)
    n = p.shape[0]
    cut = int(n * args.split)
    per_year = engine.BARS_PER_YEAR[args.tf]
    yrs_te = (n - cut) / per_year
    print(f"\n  {p}  ·  eğitim {cut} / test {n-cut} bar ({yrs_te:.2f} yıl)\n")

    keys = sorted(GRID)
    rows = []
    for vals in itertools.product(*[GRID[k] for k in keys]):  # type: ignore[call-overload]
        kw = dict(zip(keys, vals))
        w = S.xs_mom(p, longshort=True, **kw)
        d = leg_returns(w, p.ret, p.mask)
        r = d["long"] + d["short"] + d["cost"]
        te = slice(cut, None)
        rows.append({
            **kw,
            "gross_te": float(d["long"][te].sum() + d["short"][te].sum()),
            "cost_te": float(d["cost"][te].sum()),
            "net_te": float(r[te].sum()),
            "sr_tr": engine.stats(r[:cut], args.tf)["sharpe"],
            "sr_te": engine.stats(r[te], args.tf)["sharpe"],
            "dd_te": engine.stats(r[te], args.tf)["max_dd"],
            "turn": float(np.abs(np.diff(w, axis=0)).sum() / n),
            "_r": r,
        })

    N = len(rows)
    print(f"  {N} KONFİGÜRASYON — hepsi piyasa-nötr (net maruziyet ~0)\n")

    # The question is the spread-vs-cost race, so rank on gross spread and show
    # what the cost did to each one.
    by_gross = sorted(rows, key=lambda r: -r["gross_te"])
    print("  BRÜT YAYILIMA GÖRE İLK 10 (test dönemi, yıllıklandırılmış)")
    print(f"  {'lb':>4} {'k':>2} {'reb':>4} {'buf':>4} {'skip':>5} {'volAdj':>7} "
          f"{'BRÜT':>8} {'maliyet':>9} {'NET':>8} {'devir':>7} {'testSR':>7}")
    for r in by_gross[:10]:
        print(f"  {r['lookback']:>4} {r['k']:>2} {r['rebal']:>4} {r['buffer']:>4} "
              f"{r['skip']:>5} {str(r['vol_adj']):>7} "
              f"{r['gross_te']/yrs_te*100:>7.1f}% {r['cost_te']/yrs_te*100:>8.1f}% "
              f"{r['net_te']/yrs_te*100:>7.1f}% {r['turn']:>7.3f} {r['sr_te']:>+7.2f}")

    pos = [r for r in rows if r["net_te"] > 0]
    print(f"\n  Testte NET POZİTİF olan: {len(pos)}/{N}")
    for r in sorted(pos, key=lambda r: -r["net_te"])[:6]:
        print(f"    lb={r['lookback']} k={r['k']} reb={r['rebal']} buf={r['buffer']} "
              f"skip={r['skip']} volAdj={r['vol_adj']}  →  "
              f"net {r['net_te']/yrs_te*100:+.1f}%/yıl  SR {r['sr_te']:+.2f}  "
              f"DD {r['dd_te']*100:.1f}%")

    # Does hysteresis do what it is for?
    print("\n  DEVİR HIZI KOLU — buffer'ın maliyete etkisi (aynı diğer parametreler)")
    base = {"lookback": 200, "k": 5, "skip": 0, "vol_adj": False}
    for reb in (6, 24, 96):
        line = []
        for buf in (0, 3, 6):
            m = next(r for r in rows
                     if all(r[kk] == vv for kk, vv in base.items())
                     and r["rebal"] == reb and r["buffer"] == buf)
            line.append(f"buf={buf}: maliyet {m['cost_te']/yrs_te*100:>5.1f}%/yıl "
                        f"net {m['net_te']/yrs_te*100:>+5.1f}%")
        print(f"    rebal={reb:<3} " + "   ".join(line))

    # Verdict, with the trial count.
    win = max(rows, key=lambda r: r["sr_tr"])
    sr_list = [r["sr_tr"] / math.sqrt(per_year) for r in rows]
    wfa._TRIAL_VAR[0] = statistics.pvariance(sr_list) if len(sr_list) > 1 else 0.0
    ds = wfa.deflated_sharpe([float(x) for x in win["_r"][cut:]], n_trials=N)
    print(f"\n  EĞİTİM KAZANANI: lb={win['lookback']} k={win['k']} "
          f"reb={win['rebal']} buf={win['buffer']} skip={win['skip']} "
          f"volAdj={win['vol_adj']}")
    print(f"    eğitim SR {win['sr_tr']:+.2f} → test SR {win['sr_te']:+.2f}   "
          f"net {win['net_te']/yrs_te*100:+.1f}%/yıl")
    print(f"    DSR ({N} deneme) = "
          + (f"{ds['dsr']:.3f}" if ds["dsr"] is not None else "—")
          + f"   şans eşiği {ds['sr_star']:+.4f} vs gözlenen {ds['sr']:+.4f}")

    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/ls_{args.days}d_{args.tf}.json", "w") as fh:
        json.dump({"n_trials": N, "tf": args.tf, "days": args.days,
                   "years_test": yrs_te,
                   "rows": [{k: v for k, v in r.items() if k != "_r"}
                            for r in rows]}, fh)
    print(f"\n  → {OUT}/ls_{args.days}d_{args.tf}.json\n")


if __name__ == "__main__":
    main()
