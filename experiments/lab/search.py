"""Strategy search with the trial count carried through to the verdict.

    python3.12 experiments/lab/search.py --days 665 [--split 0.6]

A grid search IS a selection-bias machine — that is not a criticism of grids, it
is arithmetic. Trying 400 configurations and reporting the best one without
saying "400" is how every over-fitted backtest in the world gets published, and
today's walk-forward showed this project has been doing exactly that with coins.

So every configuration evaluated is counted, and the count goes into the verdict:

  TRAIN / TEST   the grid is scored on the first `split` of the history and the
                 winner is re-scored on the remainder, which it never saw.
  DSR            the winner's Sharpe is compared against E[max Sharpe] of N
                 null candidates, where N is the ACTUAL number of configurations
                 tried — not one, not the family count.
  PBO            CSCV over the whole return matrix: how often does the
                 in-sample-best configuration land below the out-of-sample
                 median? Near 0.5 means the ranking is noise.
  NULLS          buy_hold and inv_vol are in the grid, so "we found a strategy"
                 must beat "we held the market".

Results land in experiments/lab/results/<days>d.json for tomorrow.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import statistics
import sys
import time

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

OUT = "experiments/lab/results"

# The space. Every combination here is one trial and is counted as one.
GRID: dict[str, dict] = {
    "buy_hold": {},
    "inv_vol":  {"lookback": [50, 200], "rebal": [1, 24]},
    "ts_mom":   {"lookback": [12, 48, 200, 720], "rebal": [1, 6, 24]},
    "xs_mom":   {"lookback": [12, 48, 200, 720], "k": [3, 5, 8],
                 "longshort": [False, True], "rebal": [1, 6, 24]},
    "xs_rev":   {"lookback": [3, 6, 12, 48], "k": [3, 5, 8], "rebal": [1, 6]},
    "donchian": {"lookback": [24, 96, 288], "rebal": [1, 6]},
}
TIMEFRAMES = ["15m", "1h", "4h"]


def combos(space: dict) -> list[dict]:
    if not space:
        return [{}]
    keys = sorted(space)
    return [dict(zip(keys, v)) for v in itertools.product(*(space[k] for k in keys))]


def pbo_matrix(R: np.ndarray, blocks: int = 8) -> dict:
    """CSCV over a T x N matrix of per-bar returns, one column per config."""
    T, N = R.shape
    if N < 2 or T < blocks * 4:
        return {"pbo": None, "n_partitions": 0}
    cut = np.array_split(np.arange(T), blocks)
    below, total = 0, 0
    for train in itertools.combinations(range(blocks), blocks // 2):
        te = [b for b in range(blocks) if b not in train]
        tr_idx = np.concatenate([cut[b] for b in train])
        te_idx = np.concatenate([cut[b] for b in te])
        is_ = engine.bar_sharpe_cols(R[tr_idx])
        oos = engine.bar_sharpe_cols(R[te_idx])
        best = int(np.argmax(is_))
        rank = int(np.sum(oos <= oos[best]))          # 1 = worst
        if rank / (N + 1) <= 0.5:
            below += 1
        total += 1
    return {"pbo": below / total, "n_partitions": total}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=665)
    ap.add_argument("--split", type=float, default=0.6, help="train fraction")
    ap.add_argument("--timeframes", default=",".join(TIMEFRAMES))
    args = ap.parse_args()

    tfs = args.timeframes.split(",")
    syms = panel_mod.available(args.days)
    print(f"  {len(syms)} coin · {args.days}g · zaman dilimleri {tfs}")

    trials, t0 = [], time.time()
    for tf in tfs:
        p = panel_mod.load(syms, args.days, tf)
        n = p.shape[0]
        cut = int(n * args.split)
        print(f"\n  ── {tf}: {p} · eğitim {cut} bar / test {n - cut}")
        for fam, space in GRID.items():
            for params in combos(space):
                w = S.FAMILIES[fam](p, **params)
                r = engine.evaluate(w, p.ret, p.mask)
                tr, te = r[:cut], r[cut:]
                trials.append({
                    "tf": tf, "family": fam, "params": params,
                    "train": engine.stats(tr, tf),
                    "test": engine.stats(te, tf),
                    "full": engine.stats(r, tf),
                    "_r": r.tolist(),
                })
        print(f"     {sum(1 for t in trials if t['tf'] == tf)} konfig "
              f"({time.time()-t0:.0f}s)", flush=True)

    N = len(trials)
    print(f"\n{'='*104}")
    print(f"  {N} KONFİGÜRASYON DENENDİ — bu sayı hükmün içine giriyor")
    print(f"{'='*104}")

    ranked = sorted(trials, key=lambda t: -t["train"]["sharpe"])
    print("\n  EĞİTİMDE İLK 12 (bunlar seçim, sonuç değil)")
    print(f"  {'#':>2} {'tf':>4} {'aile':<10} {'params':<44} "
          f"{'eğitim SR':>10} {'TEST SR':>9} {'test yıllık':>12} {'test DD':>9}")
    for i, t in enumerate(ranked[:12], 1):
        ps = ",".join(f"{k}={v}" for k, v in sorted(t["params"].items())) or "—"
        print(f"  {i:>2} {t['tf']:>4} {t['family']:<10} {ps:<44} "
              f"{t['train']['sharpe']:>10.2f} {t['test']['sharpe']:>9.2f} "
              f"{t['test']['ann_ret']*100:>11.1f}% {t['test']['max_dd']*100:>8.1f}%")

    print("\n  NULL KOLLAR (test dönemi) — her sinyalin geçmesi gereken çıta")
    for t in trials:
        if t["family"] in ("buy_hold",) and t["tf"] == tfs[0]:
            print(f"    buy_hold      {t['tf']}  test SR {t['test']['sharpe']:+.2f}  "
                  f"yıllık {t['test']['ann_ret']*100:+.1f}%  DD {t['test']['max_dd']*100:.1f}%")
    best_iv = max((t for t in trials if t["family"] == "inv_vol"),
                  key=lambda t: t["train"]["sharpe"], default=None)
    if best_iv:
        print(f"    inv_vol (en iyi) {best_iv['tf']}  test SR {best_iv['test']['sharpe']:+.2f}  "
              f"yıllık {best_iv['test']['ann_ret']*100:+.1f}%")

    win = ranked[0]
    print(f"\n{'-'*104}")
    print("  EĞİTİM KAZANANI, TEST DÖNEMİNDE")
    print(f"{'-'*104}")
    ps = ",".join(f"{k}={v}" for k, v in sorted(win["params"].items())) or "—"
    print(f"  {win['family']} {win['tf']} {ps}")
    print(f"    eğitim SR {win['train']['sharpe']:+.2f}  →  TEST SR {win['test']['sharpe']:+.2f}"
          f"   yıllık {win['test']['ann_ret']*100:+.1f}%   DD {win['test']['max_dd']*100:.1f}%")

    # DSR with the honest N.
    # SCALE: deflated_sharpe works on the raw per-bar return series, so its `sr`
    # is per-bar. The trial variance it is compared against must be per-bar too.
    # stats()["sharpe"] is ANNUALISED, and feeding those in directly inflated the
    # chance bar by sqrt(bars_per_year) — it printed a threshold of +16.5 per bar
    # (+771 annualised), which is not a Sharpe, it is a unit error. Convert each
    # config's Sharpe back to bar scale using ITS OWN timeframe, since the grid
    # mixes 15m with 4h.
    sr_list = [t["train"]["sharpe"] / math.sqrt(engine.BARS_PER_YEAR[t["tf"]])
               for t in trials]
    wfa._TRIAL_VAR[0] = statistics.pvariance(sr_list) if len(sr_list) > 1 else 0.0
    te = np.array(win["_r"])[int(len(win["_r"]) * args.split):]
    ds = wfa.deflated_sharpe([float(x) for x in te], n_trials=N)
    bar = math.sqrt(engine.BARS_PER_YEAR[win["tf"]])
    print(f"\n  DEFLATED SHARPE ({N} deneme hesaba katılarak)")
    print(f"    test SR (bar) {ds['sr']:+.4f}   şans eşiği {ds['sr_star']:+.4f}"
          f"   → yıllıkta {ds['sr']*bar:+.2f} vs {ds['sr_star']*bar:+.2f}")
    print(f"    DSR = {ds['dsr']:.3f}" if ds["dsr"] is not None else "    DSR = —")

    L = min(len(t["_r"]) for t in trials)
    R = np.column_stack([np.array(t["_r"][:L]) for t in trials])
    pb = pbo_matrix(R)
    print(f"\n  PBO = {pb['pbo']:.3f}" if pb["pbo"] is not None else "\n  PBO = —",
          f"  ({pb['n_partitions']} bölüntü)")

    os.makedirs(OUT, exist_ok=True)
    slim = [{k: v for k, v in t.items() if k != "_r"} for t in trials]
    with open(f"{OUT}/{args.days}d.json", "w") as fh:
        json.dump({"n_trials": N, "split": args.split, "symbols": syms,
                   "timeframes": tfs, "trials": slim,
                   "dsr": ds, "pbo": pb}, fh)
    print(f"\n  → {OUT}/{args.days}d.json   ({time.time()-t0:.0f}s)\n")


if __name__ == "__main__":
    main()
