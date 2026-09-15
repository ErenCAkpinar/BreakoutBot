"""The three-line rule, measured the same way as the machine.

    python3.12 experiments/synth/naive.py            # all markets, table
    python3.12 experiments/synth/naive.py --md

Rule (Tur 9's oracle check, unchanged, no parameter search): at a bar close, if
the last `look` bars' return is positive and the coin is flat, go long at that
close; exit at the close `hold` bars later. Long-only, one position per coin,
every coin its own equal notional, no portfolio cap, no gates. Costs: the
repo's EXEC_COST_PER_SIDE on both sides. Two variants:

  nostop   pure horizon signal — is there anything to harvest at `hold` bars?
  sl       the noise-scaled stop: SL at `sl_sd` × the trailing 30-day SD of
           `hold`-bar returns as of the entry bar (causal, frozen at entry),
           adverse fill (bar low ≤ stop ⇒ filled at the stop, clamped into the
           bar's range). R = net return / that trade's stop distance.

Markets: synthetic null / trend / chop (seeds 1–3), bootstrap in-sample / OOS
(seeds 1–3), and REAL frames in real order — the 240d in-sample window and the
665d cache cut to before 2025-12-29 (the out-of-sample 466 days). The real OOS
row is the one that decides; it is the only row the geometry never saw.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import EXEC_COST_PER_SIDE, REGIME_WARMUP_BARS, TOKENS  # noqa: E402
from experiments.synth import gen  # noqa: E402

LOOK, HOLD = 48, 96
VOL_WINDOW = 30 * 288       # trailing 30 days for the causal stop width


def run_rule(df: pd.DataFrame, look: int = LOOK, hold: int = HOLD,
             sl_sd: float | None = None, start: int = REGIME_WARMUP_BARS,
             cost: float = EXEC_COST_PER_SIDE) -> dict:
    """One coin. Returns per-trade net returns (fraction of notional) and,
    for the stop variant, the stop distance so pnl can be read in R."""
    c = df["close"].to_numpy(float)
    lo = df["low"].to_numpy(float)
    n = len(c)
    # Stop width is CAUSAL: the trailing SD of hold-horizon returns over the
    # VOL_WINDOW bars before the entry bar, frozen at entry. (Before the review
    # of 2026-09-14 it was the SD over the whole test period — a scale that
    # future bars could change; finding 2.)
    lc_all = np.log(c)
    rh = np.full(n, np.nan)
    rh[hold:] = lc_all[hold:] - lc_all[:-hold]          # return ending at bar t
    csum = np.nancumsum(rh ** 2)
    cnt = np.cumsum(~np.isnan(rh))

    def sd_at(i: int) -> float:
        a, b = max(0, i - VOL_WINDOW), i
        k = cnt[b - 1] - (cnt[a - 1] if a > 0 else 0)
        ss = csum[b - 1] - (csum[a - 1] if a > 0 else 0.0)
        return float(np.sqrt(ss / k)) if k >= 20 else float("nan")

    sd_hold = float(np.nanstd(rh[start:]))               # reported only, never used for a stop
    rets: list[float] = []
    r_over_sl: list[float] = []
    sls: list[float] = []
    exits = {"HOLD": 0, "SL": 0}
    i = max(start, look, VOL_WINDOW)
    while i + hold < n:
        if c[i] > c[i - look]:
            entry = c[i]
            exit_p = c[i + hold]
            kind = "HOLD"
            sl = (sl_sd * sd_at(i)) if sl_sd else None    # only bars < i
            if sl is not None and np.isnan(sl):
                i += 1
                continue
            if sl is not None:
                stop = entry * (1.0 - sl)
                seg = lo[i + 1:i + hold + 1]
                hit = np.nonzero(seg <= stop)[0]
                if len(hit):
                    j = i + 1 + int(hit[0])
                    exit_p = max(stop, lo[j])          # gap-through clamp
                    kind = "SL"
                    i = j
            net = exit_p / entry - 1.0 - 2 * cost
            rets.append(net)
            if sl is not None:
                r_over_sl.append(net / sl)
                sls.append(sl)
            exits[kind] += 1
            i += hold if kind == "HOLD" else 1
        else:
            i += 1
    r = np.array(rets)
    out = {"n": len(r), "mean_pct": float(r.mean() * 100) if len(r) else float("nan"),
           "wr": float((r > 0).mean() * 100) if len(r) else float("nan"),
           "total_pct": float(r.sum() * 100), "sd_hold_pct": sd_hold * 100,
           "exits": exits}
    if sl_sd:
        # R per trade = net return / that trade's own stop distance
        out["R"] = float(np.mean(r_over_sl)) if len(r_over_sl) else float("nan")
        out["sl_pct"] = float(np.mean(sls) * 100) if sls else float("nan")
    return out


def run_market(frames: dict[str, pd.DataFrame], sl_sd: float | None) -> dict:
    per = {s: run_rule(frames[s], sl_sd=sl_sd) for s in TOKENS if s in frames}
    n = sum(v["n"] for v in per.values())
    allr = np.concatenate([np.full(v["n"], v["mean_pct"]) for v in per.values()]) if n else np.array([])
    pooled_mean = float(sum(v["mean_pct"] * v["n"] for v in per.values()) / n) if n else float("nan")
    out = {"n": n, "mean_pct": pooled_mean,
           "wr": float(sum(v["wr"] * v["n"] for v in per.values()) / n) if n else float("nan"),
           "total_pct": float(np.mean([v["total_pct"] for v in per.values()])),   # per-coin avg (equal notional)
           "per_coin": {s: round(v["total_pct"], 1) for s, v in per.items()},
           "coins_pos": sum(1 for v in per.values() if v["total_pct"] > 0)}
    if sl_sd:
        out["R"] = float(sum(v["R"] * v["n"] for v in per.values()) / n) if n else float("nan")
        out["sl_frac"] = float(np.mean([v["exits"]["SL"] / max(v["n"], 1) for v in per.values()]))
    del allr
    return out


def markets() -> list[tuple[str, dict[str, pd.DataFrame]]]:
    out: list[tuple[str, dict]] = []
    for scn in ("null", "trend", "chop"):
        for sd in (1, 2, 3):
            out.append((f"synth {scn} s{sd}", gen.simulate(gen.SCENARIOS[scn], 240, sd)))
    real_in = gen.load_real(240)
    real_oos = gen.load_real(665, before="2025-12-29")
    for sd in (1, 2, 3):
        out.append((f"bootstrap in-sample s{sd}", gen.bootstrap(real_in, 240, sd)))
        out.append((f"bootstrap OOS s{sd}", gen.bootstrap(real_oos, 240, sd)))
    out.append(("GERÇEK 240g in-sample (2025-11→2026-08)", real_in))
    out.append(("GERÇEK OOS (2024-09→2025-12, gerçek sıra)", real_oos))
    return out


def main() -> None:
    md = "--md" in sys.argv
    rows = []
    for label, fr in markets():
        a = run_market(fr, None)
        b = run_market(fr, 1.0)
        rows.append((label, a, b))
    if md:
        print("| piyasa | n | stopsuz: ort %/işlem | WR | coin-ort toplam % | +coin | stop 1×SD: R | SL payı | toplam % |")
        print("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
        for label, a, b in rows:
            print(f"| {label} | {a['n']} | {a['mean_pct']:+.3f} | {a['wr']:.0f}% | {a['total_pct']:+.0f}% | {a['coins_pos']}/5 | **{b['R']:+.3f}** | {b['sl_frac']:.0%} | {b['total_pct']:+.0f}% |")
    else:
        print(f"  {'piyasa':<44} {'n':>5} {'ort%/işlem':>11} {'WR':>5} {'toplam%':>8} {'+coin':>5} | {'R(stop)':>8} {'SL%':>5} {'toplam%':>8}")
        print("  " + "-" * 112)
        for label, a, b in rows:
            print(f"  {label:<44} {a['n']:>5} {a['mean_pct']:+11.3f} {a['wr']:4.0f}% {a['total_pct']:+8.0f} {a['coins_pos']:>4}/5 | {b['R']:+8.3f} {b['sl_frac']:4.0%} {b['total_pct']:+8.0f}")
    # group summaries
    def grp(prefix):
        sel = [(a, b) for lab, a, b in rows if lab.startswith(prefix)]
        if not sel:
            return
        n = sum(a["n"] for a, _ in sel)
        m = sum(a["mean_pct"] * a["n"] for a, _ in sel) / n
        R = sum(b["R"] * b["n"] for _, b in sel) / n
        print(f"  {'Σ ' + prefix:<44} {n:>5} {m:+11.3f} {'':>5} {'':>8} {'':>5} | {R:+8.3f}")
    print()
    for pfx in ("synth null", "synth trend", "synth chop", "bootstrap in-sample", "bootstrap OOS"):
        grp(pfx)


if __name__ == "__main__":
    main()
