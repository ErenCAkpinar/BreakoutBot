"""Walk-forward curation with a selection-bias correction.

    python3.12 experiments/wfa.py --days 665 --pick 5 [--folds 6]

WHY THIS EXISTS
---------------
Every curation this project has run took the same shape: replay N coins on one
window, rank them, keep the top few. That number is not an estimate of anything.
The top 5 of 23 noisy candidates look good *by construction* — you would get a
flattering leaderboard from 23 coin flips. `config.py` already documents the
symptom (90d ranks anti-correlate with the next period at -0.31) without naming
the disease.

Three things are computed here, and they answer three different questions.

1. WALK-FORWARD — "does the selection RULE work?"
   Split the history into sequential folds. Select the universe using only data
   BEFORE each fold, then score it on the fold itself. Concatenate those
   out-of-sample segments. Nothing in the reported number was chosen with
   knowledge of it. Compared against two null arms: all candidates equal-weight,
   and a random pick of the same size (so "we picked well" has to beat "we
   picked anything").

2. PBO — "how likely is this procedure to be fooling me?"
   Combinatorially Symmetric Cross-Validation (Bailey, Borwein, López de Prado,
   López de Prado & Zhu). Split the position matrix into S blocks, take every
   balanced train/test split, and ask how often the configuration that ranked
   best in-sample lands BELOW median out-of-sample. That frequency is the
   Probability of Backtest Overfitting. Above ~0.5 the procedure is worse than
   guessing.

3. DEFLATED SHARPE — "does the winner survive having been the winner?"
   Bailey & López de Prado. The expected maximum Sharpe of N independent random
   candidates is strictly positive, so the best of 23 must clear that bar before
   it means anything. DSR is the probability the true Sharpe exceeds zero once
   the number of trials, the sample length, skew and kurtosis are accounted for.

PURGING AND EMBARGO
-------------------
A position opened before a fold boundary and closed after it straddles the split
and would leak. Positions are assigned to a fold by their OPEN time and any that
cross the boundary are purged; an embargo then drops whatever starts inside
`--embargo-bars` after the boundary. The default embargo is 192 bars because
`bars_held` resets on the TP1 transition, making the real maximum hold
2 x TIMEOUT_BARS — measured max on the 665d window: 187 bars.
"""
from __future__ import annotations

import argparse
import glob
import itertools
import json
import math
import os
import random
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

SRC = "experiments/candidates"
BAR_MS = 300_000


# ── statistics ───────────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def sharpe(rs: list[float]) -> float:
    """Per-position Sharpe. Not annualised: every figure here is per position, so
    annualising would only invite comparison with numbers that are not."""
    if len(rs) < 3:
        return 0.0
    sd = statistics.pstdev(rs)
    return (statistics.fmean(rs) / sd) if sd > 1e-12 else 0.0


def _moments(rs: list[float]) -> tuple[float, float]:
    """(skew, kurtosis) — kurtosis is the non-excess convention DSR expects."""
    n = len(rs)
    if n < 4:
        return 0.0, 3.0
    m = statistics.fmean(rs)
    sd = statistics.pstdev(rs)
    if sd < 1e-12:
        return 0.0, 3.0
    z = [(r - m) / sd for r in rs]
    return sum(v ** 3 for v in z) / n, sum(v ** 4 for v in z) / n


def expected_max_sharpe(n_trials: int, var_sharpe: float) -> float:
    """E[max Sharpe] over `n_trials` candidates whose true Sharpe is zero.

    Bailey & López de Prado's approximation from the Gaussian extreme-value
    result. This is the bar the winner has to clear to mean anything: with 23
    candidates it is emphatically not zero.
    """
    if n_trials < 2 or var_sharpe <= 0:
        return 0.0
    e = 0.5772156649015329                      # Euler-Mascheroni
    z1 = _inv_norm(1.0 - 1.0 / n_trials)
    z2 = _inv_norm(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(var_sharpe) * ((1 - e) * z1 + e * z2)


def _inv_norm(p: float) -> float:
    """Acklam's inverse normal CDF — plenty accurate for this, no SciPy needed."""
    p = min(max(p, 1e-12), 1 - 1e-12)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q, r = p - 0.5, (p - 0.5) ** 2
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def deflated_sharpe(rs: list[float], n_trials: int, sr_star: float | None = None) -> dict:
    """P(true Sharpe > threshold), correcting for trials, length, skew, kurtosis."""
    n = len(rs)
    if n < 8:
        return {"sr": 0.0, "sr_star": 0.0, "dsr": None, "n": n}
    sr = sharpe(rs)
    sk, ku = _moments(rs)
    if sr_star is None:
        # The bar: E[max Sharpe] of n_trials null candidates, using the observed
        # cross-candidate dispersion as the variance of the trials.
        sr_star = expected_max_sharpe(n_trials, _TRIAL_VAR[0])
    denom = math.sqrt(max(1e-12, 1 - sk * sr + (ku - 1) / 4.0 * sr ** 2))
    z = (sr - sr_star) * math.sqrt(n - 1) / denom
    return {"sr": sr, "sr_star": sr_star, "dsr": _norm_cdf(z), "n": n}


_TRIAL_VAR = [0.0]      # set from the observed spread of candidate Sharpes


# ── data ─────────────────────────────────────────────────────────────────────

def load(days: int) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for p in sorted(glob.glob(f"{SRC}/*_{days}d.json")):
        d = json.load(open(p))
        if d["positions"]:
            out[d["symbol"]] = d["positions"]
    return out


def to_r(positions: list[dict], risk: float = 10.0) -> list[float]:
    return [p["pnl"] / risk for p in positions]


# ── walk-forward ─────────────────────────────────────────────────────────────

def walk_forward(cand: dict[str, list[dict]], folds: int, pick: int,
                 embargo_bars: int, drop: int = 8, seed: int = 7) -> dict:
    """Select on the past, score on the next fold, concatenate the out-of-sample."""
    all_ts = sorted(p["ts_open"] for ps in cand.values() for p in ps)
    if len(all_ts) < folds * 10:
        raise SystemExit("çok az pozisyon — fold sayısını düşür")
    t0, t1 = all_ts[0], all_ts[-1]
    edges = [t0 + (t1 - t0) * i / folds for i in range(folds + 1)]
    embargo = embargo_bars * BAR_MS
    rnd = random.Random(seed)

    picked_oos: list[float] = []
    equal_oos: list[float] = []
    random_oos: list[float] = []
    keep_oos: list[float] = []
    rows = []

    for k in range(1, folds):                    # fold 0 is training only
        lo, hi = edges[k], edges[k + 1]

        def slice_of(sym: str, a: float, b: float) -> list[dict]:
            # Assign by OPEN time and PURGE anything still open at the boundary:
            # a position that straddles the split saw both sides.
            return [p for p in cand[sym]
                    if a <= p["ts_open"] < b and p["ts_close"] < b]

        train = {s: [p for p in cand[s] if p["ts_close"] <= lo - embargo]
                 for s in cand}
        train = {s: v for s, v in train.items() if len(v) >= 8}
        if len(train) < pick:
            continue

        ranked = sorted(train, key=lambda s: statistics.fmean(to_r(train[s])),
                        reverse=True)
        chosen = ranked[:pick]
        # Picking the best and dropping the worst are DIFFERENT questions, and a
        # ranking can be uninformative at the top while still separating the tail.
        # Every curation this project ran conflated them, so both are measured.
        keep = ranked[:max(1, len(ranked) - drop)]
        rand_pick = rnd.sample(sorted(cand), min(pick, len(cand)))

        f_pick = [r for s in chosen for r in to_r(slice_of(s, lo, hi))]
        f_all = [r for s in cand for r in to_r(slice_of(s, lo, hi))]
        f_rand = [r for s in rand_pick for r in to_r(slice_of(s, lo, hi))]
        f_keep = [r for s in keep for r in to_r(slice_of(s, lo, hi))]
        picked_oos += f_pick
        equal_oos += f_all
        random_oos += f_rand
        keep_oos += f_keep
        rows.append({
            "fold": k, "n_train": sum(len(v) for v in train.values()),
            "chosen": chosen, "n_oos": len(f_pick),
            "r_pick": statistics.fmean(f_pick) if f_pick else 0.0,
            "r_all": statistics.fmean(f_all) if f_all else 0.0,
            "r_rand": statistics.fmean(f_rand) if f_rand else 0.0,
            "r_keep": statistics.fmean(f_keep) if f_keep else 0.0,
        })

    return {"rows": rows, "picked": picked_oos, "equal": equal_oos,
            "random": random_oos, "keep": keep_oos}


# ── PBO via CSCV ─────────────────────────────────────────────────────────────

def pbo(cand: dict[str, list[dict]], blocks: int = 8) -> dict:
    """Probability of Backtest Overfitting.

    Build a position matrix (rows = time blocks, cols = candidates), take every
    balanced train/test partition of the blocks, and record the out-of-sample
    RANK of whichever candidate won in-sample. PBO is the share of partitions
    where that winner finished below the out-of-sample median.
    """
    syms = sorted(cand)
    all_ts = sorted(p["ts_open"] for ps in cand.values() for p in ps)
    t0, t1 = all_ts[0], all_ts[-1]
    edges = [t0 + (t1 - t0) * i / blocks for i in range(blocks + 1)]

    # perf[b][s] — mean R of candidate s inside block b
    perf: list[dict[str, float]] = []
    for b in range(blocks):
        lo, hi = edges[b], edges[b + 1]
        row = {}
        for s in syms:
            rs = to_r([p for p in cand[s] if lo <= p["ts_open"] < hi])
            row[s] = statistics.fmean(rs) if rs else 0.0
        perf.append(row)

    half = blocks // 2
    logits, below = [], 0
    combos = list(itertools.combinations(range(blocks), half))
    for train_idx in combos:
        test_idx = [b for b in range(blocks) if b not in train_idx]
        is_ = {s: statistics.fmean([perf[b][s] for b in train_idx]) for s in syms}
        oos = {s: statistics.fmean([perf[b][s] for b in test_idx]) for s in syms}
        best = max(is_, key=lambda s: is_[s])
        order = sorted(syms, key=lambda s: oos[s])
        rank = order.index(best) + 1
        w = rank / (len(syms) + 1)
        if w <= 0.5:
            below += 1
        w = min(max(w, 1e-6), 1 - 1e-6)
        logits.append(math.log(w / (1 - w)))
    return {"pbo": below / len(combos), "n_partitions": len(combos),
            "median_logit": statistics.median(logits)}


# ── report ───────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=665)
    ap.add_argument("--pick", type=int, default=5)
    ap.add_argument("--folds", type=int, default=6)
    ap.add_argument("--blocks", type=int, default=8)
    ap.add_argument("--embargo-bars", type=int, default=192)
    ap.add_argument("--drop", type=int, default=8,
                    help="en kötü N adayı ele (üst-seçim yerine alt-eleme kolu)")
    args = ap.parse_args()

    cand = load(args.days)
    if not cand:
        raise SystemExit(f"{SRC}/ boş — önce experiments/gen_candidates.py koş")

    sharpes = [sharpe(to_r(v)) for v in cand.values() if len(v) >= 8]
    _TRIAL_VAR[0] = statistics.pvariance(sharpes) if len(sharpes) > 1 else 0.0

    print(f"\n{'='*100}")
    print(f"  WALK-FORWARD KÜRASYON — {len(cand)} aday · {args.days}g · "
          f"{args.folds} fold · ilk {args.pick} · embargo {args.embargo_bars} bar")
    print(f"{'='*100}")

    print(f"\n  {'sembol':<12} {'n':>5} {'ort R':>8} {'Sharpe':>8} {'toplam $':>10}")
    for s in sorted(cand, key=lambda s: -statistics.fmean(to_r(cand[s]))):
        rs = to_r(cand[s])
        print(f"  {s:<12} {len(rs):>5} {statistics.fmean(rs):>+8.3f} "
              f"{sharpe(rs):>8.3f} {sum(p['pnl'] for p in cand[s]):>+10.2f}")
    print("\n  ⚠️  Bu tablo TAM ÖRNEKLEM — sıralamak için değil, sadece manzara.")

    wf = walk_forward(cand, args.folds, args.pick, args.embargo_bars,
                      drop=args.drop)
    print(f"\n{'-'*100}")
    print("  1 · WALK-FORWARD — seçim kuralı örneklem DIŞINDA ne yapıyor?")
    print(f"{'-'*100}")
    print(f"  {'fold':>4} {'eğitim n':>9} {'OOS n':>6} {'seçilen R':>10} "
          f"{'hepsi R':>9} {'rastgele R':>11} {'alt-elem R':>11}   seçilenler")
    for r in wf["rows"]:
        print(f"  {r['fold']:>4} {r['n_train']:>9} {r['n_oos']:>6} "
              f"{r['r_pick']:>+10.3f} {r['r_all']:>+9.3f} {r['r_rand']:>+11.3f} "
              f"{r['r_keep']:>+11.3f}   "
              f"{','.join(x.replace('USDT','') for x in r['chosen'])}")
    for name, key in (("SEÇİLEN (ilk %d)" % args.pick, "picked"),
                      ("EN KÖTÜ %d ELENDİ" % args.drop, "keep"),
                      ("hepsi (eşit ağırlık)", "equal"),
                      ("rastgele aynı sayıda", "random")):
        rs = wf[key]
        if rs:
            print(f"  {name:<24} n={len(rs):<5} ort {statistics.fmean(rs):+.3f}R   "
                  f"Sharpe {sharpe(rs):+.3f}")
    picked, equal = wf["picked"], wf["equal"]
    if picked and equal:
        d = statistics.fmean(picked) - statistics.fmean(equal)
        se = math.sqrt(statistics.pvariance(picked) / len(picked)
                       + statistics.pvariance(equal) / len(equal))
        print(f"  → seçim primi: {d:+.3f}R   SE {se:.3f}   t={d/se if se else 0:.2f}"
              f"   {'✅ gürültüden ayrılıyor' if se and abs(d) > 2*se else '~ ayırt edilemiyor'}")

    p = pbo(cand, args.blocks)
    print(f"\n{'-'*100}")
    print("  2 · PBO — bu seçim yordamı beni ne sıklıkta kandırır? (CSCV)")
    print(f"{'-'*100}")
    print(f"  {p['n_partitions']} dengeli bölüntü · {args.blocks} blok")
    print(f"  PBO = {p['pbo']:.3f}   (0.5 üstü = yazı-turadan kötü)   "
          f"medyan logit {p['median_logit']:+.3f}")
    print("  → " + ("✅ yordam bilgi taşıyor" if p["pbo"] < 0.35 else
                    "⚠️  sınırda" if p["pbo"] < 0.5 else
                    "❌ aşırı uydurma — sıralama örneklem dışında tutmuyor"))

    if picked:
        ds = deflated_sharpe(picked, n_trials=len(cand))
        print(f"\n{'-'*100}")
        print("  3 · DEFLATED SHARPE — kazanan, kazanan olmayı hak ediyor mu?")
        print(f"{'-'*100}")
        print(f"  gözlenen Sharpe {ds['sr']:+.4f}   "
              f"{len(cand)} denemenin şans eşiği {ds['sr_star']:+.4f}")
        print(f"  DSR = {ds['dsr']:.3f}" if ds["dsr"] is not None else "  DSR = —")
        print("  → " + ("✅ şans eşiğini aşıyor" if (ds["dsr"] or 0) > 0.95 else
                        "❌ denenen aday sayısı hesaba katılınca sıfırdan ayırt edilemiyor"))
    print(f"\n{'='*100}\n")


if __name__ == "__main__":
    main()
