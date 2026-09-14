"""Cross-sectional long-short momentum, 4h → 1d, measured everywhere (DEFTER Tur 12).

    python3.12 experiments/synth/xs_mom.py                     # 1-day hold
    python3.12 experiments/synth/xs_mom.py --hold=1 --hold=3 --hold=5 --md

Arm (fixed before measurement, no search): at every 00:00 UTC close rank the
universe by the trailing 4h log return; long the top 5, short the bottom 5,
equal weight, dollar-neutral, gross 100%. With --hold=N the book is N daily
tranches each held N days (Jegadeesh–Titman), marked daily, so turnover — and
its cost — falls by N while the number of daily observations does not. Cost
on turnover only: EXEC_COST_PER_SIDE × Σ|Δw|. Funding is not modelled.

Context rows: top-5 long-only, equal-weight everything (buy & hold), and a
RANDOM ranking with the same book — the same turnover and no information, so
its net return is the cost floor the signal has to clear.

Markets: synthetic null_mart / trend / chop with the real 23-coin cross-section
(σ, β, price from the 665d cache), bootstrap in-sample / OOS (joint blocks keep
the cross-section), and the REAL frames in real order — in-sample 240d and the
out-of-sample 466 days. The real OOS row decides.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import _FULL_UNIVERSE, EXEC_COST_PER_SIDE, REGIME_WARMUP_BARS  # noqa: E402
from experiments.synth import gen  # noqa: E402

LOOK, HOLD, K = 48, 288, 5
UNIVERSE = list(_FULL_UNIVERSE)


def panel(frames: dict[str, pd.DataFrame], symbols: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """log close [t, s] on the common ts grid, plus the ts vector."""
    syms = [s for s in symbols if s in frames]
    ts = None
    for s in syms:
        t = set(frames[s]["ts"].astype(int))
        ts = t if ts is None else ts & t
    ts_arr = np.array(sorted(ts or set()), dtype=np.int64)
    cols = []
    for s in syms:
        f = frames[s].set_index(frames[s]["ts"].astype(int))
        cols.append(np.log(f.loc[ts_arr, "close"].to_numpy(float)))
    return np.column_stack(cols), ts_arr


def daily_points(ts: np.ndarray, start: int) -> np.ndarray:
    """Indices of the 00:00 UTC bars from `start` on (one per day)."""
    hours = (ts // 3_600_000) % 24
    mins = (ts // 60_000) % 60
    cand = np.nonzero((hours == 0) & (mins == 0))[0]
    return cand[cand >= max(start, LOOK)]


def target_weights(lc: np.ndarray, i: int, mode: str, rng: np.random.Generator | None) -> np.ndarray:
    n_s = lc.shape[1]
    sig = lc[i] - lc[i - LOOK]
    if mode == "rand":
        sig = rng.standard_normal(n_s)  # type: ignore[union-attr]
    order = np.argsort(sig)
    w = np.zeros(n_s)
    if mode == "ew":
        w[:] = 1.0 / n_s
    elif mode == "long":
        w[order[-K:]] = 1.0 / K
    else:
        w[order[-K:]] = 0.5 / K
        w[order[:K]] = -0.5 / K
    return w


def run_book(lc: np.ndarray, pts: np.ndarray, mode: str, rng: np.random.Generator | None = None,
             cost: float = EXEC_COST_PER_SIDE, hold_days: int = 1) -> dict:
    """Daily-marked book of `hold_days` overlapping tranches (Jegadeesh–Titman).

    pts are the daily 00:00 UTC bar indices. Tranche j (= day index mod
    hold_days) is re-ranked on its day and held hold_days days; the book weight
    is the tranche average, so gross stays 100% and daily turnover is one
    tranche's rebalance. Cost is charged on the book's daily turnover only.
    mode: 'ls' long-short top/bottom K · 'long' top-K · 'ew' equal weight ·
    'rand' random ranking long-short.
    """
    n_s = lc.shape[1]
    tr = np.zeros((hold_days, n_s))          # current weights per tranche
    w_book = np.zeros(n_s)
    rets, longs, shorts, costs = [], [], [], []
    for d in range(len(pts) - 1):
        i, nxt = pts[d], pts[d + 1]
        tr[d % hold_days] = target_weights(lc, i, mode, rng)
        w_new = tr.mean(axis=0)
        turnover = float(np.abs(w_new - w_book).sum())
        r = np.exp(lc[nxt] - lc[i]) - 1.0                  # next day's simple return
        gross_long = float(np.sum(np.clip(w_new, 0, None) * r))
        gross_short = float(np.sum(np.clip(w_new, None, 0) * r))
        c = cost * turnover
        rets.append(gross_long + gross_short - c)
        longs.append(gross_long)
        shorts.append(gross_short)
        costs.append(c)
        # weights drift with the day's returns before the next rebalance
        tr = tr * (1 + r)
        w_book = w_new * (1 + r)
    r_arr = np.array(rets)
    return {"r": r_arr, "long": np.array(longs), "short": np.array(shorts), "cost": np.array(costs)}


def stats(b: dict) -> dict:
    r = b["r"]
    n = len(r)
    mu = r.mean()
    se = r.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    eq = np.cumprod(1 + r)
    dd = float(((eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq)).min())
    return {"n_gün": n, "net_%/gün": mu * 100, "t": mu / se if se else np.nan,
            "yıllık_%": ((1 + mu) ** 365 - 1) * 100, "Sharpe": mu / r.std(ddof=1) * np.sqrt(365) if n > 1 else np.nan,
            "toplam_%": (eq[-1] - 1) * 100, "maxDD_%": dd * 100,
            "uzun_bacak_%/g": b["long"].mean() * 100, "kısa_bacak_%/g": b["short"].mean() * 100,
            "maliyet_%/g": b["cost"].mean() * 100}


def evaluate(frames: dict[str, pd.DataFrame], start: int, seed: int = 0,
             hold_days: int = 1) -> dict[str, dict]:
    lc, ts = panel(frames, UNIVERSE)
    pts = daily_points(ts, start)
    rng = np.random.default_rng(seed)
    return {"ls": stats(run_book(lc, pts, "ls", hold_days=hold_days)),
            "rand": stats(run_book(lc, pts, "rand", rng, hold_days=hold_days)),
            "long": stats(run_book(lc, pts, "long", hold_days=hold_days)),
            "ew": stats(run_book(lc, pts, "ew", hold_days=hold_days))}


def markets() -> list[tuple[str, dict, int]]:
    coins = gen.coins_from_real(665)
    out: list[tuple[str, dict, int]] = []
    for scn in ("null_mart", "trend", "chop"):
        for sd in (1, 2, 3):
            out.append((f"synth {scn} s{sd}", gen.simulate(gen.SCENARIOS[scn], 240, sd, coins=coins), REGIME_WARMUP_BARS))
    real_in = gen.load_real(240, symbols=UNIVERSE + ["BTCUSDT"]) if all(
        os.path.exists(os.path.join(gen.CACHE, f"{s}_240d+40w.pkl")) for s in UNIVERSE) else None
    real_all = gen.load_real(665, symbols=UNIVERSE + ["BTCUSDT"])
    real_oos = gen.load_real(665, symbols=UNIVERSE + ["BTCUSDT"], before="2025-12-29")
    real_ins = gen.load_real(665, symbols=UNIVERSE + ["BTCUSDT"], after="2025-12-29")
    for sd in (1, 2, 3):
        out.append((f"bootstrap in-sample s{sd}", gen.bootstrap(real_ins, 240, sd), REGIME_WARMUP_BARS))
        out.append((f"bootstrap OOS s{sd}", gen.bootstrap(real_oos, 240, sd), REGIME_WARMUP_BARS))
    out.append(("GERÇEK in-sample (2025-12-29→2026-08, 665g cache)", real_ins, 0))
    out.append(("GERÇEK OOS (2024-09→2025-12, gerçek sıra)", real_oos, 0))
    out.append(("GERÇEK tümü (2024-09→2026-08)", real_all, 0))
    if real_in is not None:
        out.append(("GERÇEK 240g cache (2025-11→2026-08)", real_in, REGIME_WARMUP_BARS))
    return out


def main() -> None:
    md = "--md" in sys.argv
    holds = [int(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--hold=")] or [1]
    rows = []
    mk = markets()
    for hold in holds:
      for label, fr, start in mk:
        ev = evaluate(fr, start, hold_days=hold)
        rows.append({"piyasa": label, "tutuş_g": hold, "n_gün": ev["ls"]["n_gün"],
                     "LS net %/g": ev["ls"]["net_%/gün"], "t": ev["ls"]["t"], "Sharpe": ev["ls"]["Sharpe"],
                     "toplam %": ev["ls"]["toplam_%"], "maxDD %": ev["ls"]["maxDD_%"],
                     "uzun %/g": ev["ls"]["uzun_bacak_%/g"], "kısa %/g": ev["ls"]["kısa_bacak_%/g"],
                     "maliyet %/g": ev["ls"]["maliyet_%/g"],
                     "RASTGELE net %/g": ev["rand"]["net_%/gün"],
                     "top5-uzun %/g": ev["long"]["net_%/gün"], "EW %/g": ev["ew"]["net_%/gün"]})
        print(f"  h={hold} {label:44s} LS {ev['ls']['net_%/gün']:+.3f}%/g t {ev['ls']['t']:+.2f}  rand {ev['rand']['net_%/gün']:+.3f}  long {ev['long']['net_%/gün']:+.3f}  ew {ev['ew']['net_%/gün']:+.3f}", file=sys.stderr, flush=True)
    df = pd.DataFrame(rows)
    if md:
        cols = list(df.columns)
        def cell(v):
            return (("" if np.isnan(v) else f"{v:.3f}") if isinstance(v, float) else str(v))
        print("| " + " | ".join(cols) + " |")
        print("|" + "|".join("--:" for _ in cols) + "|")
        for _, r in df.iterrows():
            print("| " + " | ".join(cell(r[c]) for c in cols) + " |")
    else:
        pd.set_option("display.width", 250)
        print(df.to_string(index=False, float_format=lambda x: f"{x:.3f}"))


if __name__ == "__main__":
    main()
