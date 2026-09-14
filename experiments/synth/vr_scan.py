"""Where, if anywhere, does real data have VR > 1? (DEFTER Tur 11)

    python3.12 experiments/synth/vr_scan.py            # tables
    python3.12 experiments/synth/vr_scan.py --md       # markdown for DEFTER

Variance ratio VR(q) = Var(r_q) / (q · Var(r_base)) on overlapping q-period
returns, with the Lo–MacKinlay (1988) heteroskedasticity-robust z*. VR > 1 is
positive autocorrelation of base-period returns inside the horizon — the only
thing a trailing exit can harvest; VR < 1 is mean reversion. Three bases so
5-minute microstructure (bid-ask bounce pushes short-horizon VR below 1) can be
told apart from anything a strategy could use: 5m, 1h, 4h.

Then the direct question: does sign(r_L) predict r_H? The long-short naive
momentum return sign(r_L)·r_H on non-overlapping H windows, pooled across
coins, with its t. VR says "autocorrelation exists"; this says "a rule earns".

Data: the repo's own cached frames — 23 coins × 2024-09 → 2026-08 (665d+40w)
and the live five + BTC × 2020-10 → 2026-08 (2095d+40w). Period splits show
whether autocorrelation is a regime rather than a property.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import _FULL_UNIVERSE, TOKENS  # noqa: E402

CACHE = os.path.join(ROOT, "backtests", "data")
BAR_MS = 300_000
COST = 0.00075          # config.EXEC_COST_PER_SIDE

# horizon grid, fixed before measurement (DEFTER Tur 11)
HORIZONS = [("15m", 3), ("30m", 6), ("1h", 12), ("2h", 24), ("4h", 48), ("8h", 96),
            ("12h", 144), ("1d", 288), ("2d", 576), ("5d", 1440), ("10d", 2880), ("15d", 4320)]
BASES = [("5m", 1), ("1h", 12), ("4h", 48)]
MOM_L = [("1h", 12), ("4h", 48), ("1d", 288), ("5d", 1440)]
MOM_H = [("1h", 12), ("4h", 48), ("8h", 96), ("1d", 288), ("5d", 1440)]


def load(sym: str, days: int) -> pd.DataFrame:
    df = pd.read_pickle(os.path.join(CACHE, f"{sym}_{days}d+40w.pkl"))
    return df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)


def log_close(df: pd.DataFrame, step: int) -> np.ndarray:
    """Log close sampled every `step` 5m bars (calendar-aligned resample)."""
    c = np.log(df["close"].to_numpy(float))
    return c[::step]


def vr_lm(lc: np.ndarray, q: int) -> tuple[float, float]:
    """Lo–MacKinlay VR(q) and heteroskedasticity-robust z* on log prices lc."""
    r = np.diff(lc)
    n = len(r)
    if n < 4 * q:
        return float("nan"), float("nan")
    mu = r.mean()
    d = r - mu
    var1 = float(np.mean(d ** 2))
    rq = lc[q:] - lc[:-q]
    varq = float(np.mean((rq - q * mu) ** 2)) / q
    vr = varq / var1
    # θ(q) = Σ_{j=1}^{q-1} [2(q−j)/q]² δ_j,  δ_j = Σ d_t² d_{t−j}² / (Σ d_t²)²
    d2 = d ** 2
    den = float(np.sum(d2)) ** 2
    theta = 0.0
    for j in range(1, q):
        delta = float(np.sum(d2[j:] * d2[:-j])) / den
        theta += (2.0 * (q - j) / q) ** 2 * delta
    z = (vr - 1.0) / np.sqrt(theta) if theta > 0 else float("nan")
    return vr, z


def vr_profile(df: pd.DataFrame) -> dict[tuple[str, str], tuple[float, float]]:
    """{(base, horizon) → (VR, z*)} for every base ≤ horizon."""
    out = {}
    for bname, bstep in BASES:
        lc = log_close(df, bstep)
        for hname, hbars in HORIZONS:
            if hbars % bstep or hbars // bstep < 2:
                continue
            out[(bname, hname)] = vr_lm(lc, hbars // bstep)
    return out


def momentum_matrix(df: pd.DataFrame) -> dict[tuple[str, str], np.ndarray]:
    """{(L, H) → array of sign(r_L)·r_H over non-overlapping H windows}."""
    lc = np.log(df["close"].to_numpy(float))
    out = {}
    for lname, L in MOM_L:
        for hname, H in MOM_H:
            idx = np.arange(L, len(lc) - H, H)
            rL = lc[idx] - lc[idx - L]
            rH = lc[idx + H] - lc[idx]
            out[(lname, hname)] = np.sign(rL) * rH
    return out


def pooled_vr(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    prof = {s: vr_profile(f) for s, f in frames.items()}
    for bname, _ in BASES:
        for hname, _ in HORIZONS:
            vals = [(prof[s][(bname, hname)]) for s in prof if (bname, hname) in prof[s]]
            if not vals:
                continue
            vr = np.array([v for v, _ in vals])
            z = np.array([zz for _, zz in vals])
            rows.append({"taban": bname, "ufuk": hname, "VR": vr.mean(),
                         "SE": vr.std(ddof=1) / np.sqrt(len(vr)) if len(vr) > 1 else np.nan,
                         "min": vr.min(), "max": vr.max(),
                         "z>1.96": int((z > 1.96).sum()), "z<-1.96": int((z < -1.96).sum()), "n_coin": len(vr)})
    return pd.DataFrame(rows)


def pooled_momentum(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    mats = {s: momentum_matrix(f) for s, f in frames.items()}
    rows = []
    for lname, _ in MOM_L:
        for hname, _ in MOM_H:
            allv = np.concatenate([mats[s][(lname, hname)] for s in mats])
            per_coin = np.array([mats[s][(lname, hname)].mean() for s in mats])
            rows.append({"L": lname, "H": hname, "ort_%": allv.mean() * 100,
                         "t_havuz": allv.mean() / (allv.std(ddof=1) / np.sqrt(len(allv))),
                         "coin+": int((per_coin > 0).sum()), "n_coin": len(per_coin), "n": len(allv)})
    return pd.DataFrame(rows)


def split_periods(df: pd.DataFrame, freq: str) -> dict[str, pd.DataFrame]:
    t = pd.to_datetime(df["ts"], unit="ms")          # naive UTC: to_period drops tz anyway
    key = t.dt.to_period(freq).astype(str)
    return {k: g.reset_index(drop=True) for k, g in df.groupby(key) if len(g) > 288 * 20}


def period_table(frames: dict[str, pd.DataFrame], freq: str,
                 cells=(("1h", "1d"), ("1h", "5d"), ("4h", "5d"), ("5m", "4h"))) -> pd.DataFrame:
    per: dict[str, dict] = {}
    for s, f in frames.items():
        for pk, g in split_periods(f, freq).items():
            prof = vr_profile(g)
            lc = np.log(g["close"].to_numpy(float))
            per.setdefault(pk, {})[s] = (prof, (lc[-1] - lc[0]) * 100)
    rows = []
    for pk in sorted(per):
        row = {"dönem": pk, "n_coin": len(per[pk]),
               "getiri_ort_%": np.mean([v[1] for v in per[pk].values()])}
        for b, h in cells:
            vals = [v[0][(b, h)][0] for v in per[pk].values() if (b, h) in v[0]]
            zs = [v[0][(b, h)][1] for v in per[pk].values() if (b, h) in v[0]]
            row[f"VR({h};{b})"] = np.nanmean(vals) if vals else np.nan
            row[f"z>2 {h};{b}"] = int(np.sum(np.array(zs) > 1.96)) if zs else 0
        rows.append(row)
    return pd.DataFrame(rows)


def fmt(df: pd.DataFrame, md: bool) -> str:
    if md:
        return df.to_markdown(index=False, floatfmt=".3f")
    return df.to_string(index=False, float_format=lambda x: f"{x:.3f}")


def main() -> None:
    md = "--md" in sys.argv
    pd.set_option("display.width", 200)
    h = (lambda t: f"\n### {t}\n") if md else (lambda t: f"\n══ {t} ══")

    uni = {s: load(s, 665) for s in _FULL_UNIVERSE}
    live = {s: load(s, 2095) for s in list(TOKENS) + ["BTCUSDT"]}

    print(h("A. VR profili — 23 coin, 2024-09 → 2026-08 (havuz ortalama ± coin-arası SE; z*>1.96 / z*<−1.96 coin sayısı)"))
    a = pooled_vr(uni)
    print(fmt(a.pivot(index="ufuk", columns="taban", values="VR").reindex([n for n, _ in HORIZONS]).reset_index(), md))
    print()
    print(fmt(a, md))

    print(h("B. Doğrudan hasat — sign(r_L)·r_H uzun-kısa, 23 coin, 2024-09 → 2026-08 (örtüşmeyen H)"))
    print(fmt(pooled_momentum(uni), md))

    print(h("C. Çeyrek bazında — 23 coin, 665g"))
    print(fmt(period_table(uni, "Q"), md))

    print(h("D. Yıl bazında — canlı 5 coin + BTC, 2020-10 → 2026-08"))
    print(fmt(period_table(live, "Y"), md))

    print(h("E. Doğrudan hasat, yıl bazında — canlı 5 + BTC (L=4h→H=8h ve L=1d→H=1d ve L=5d→H=5d)"))
    rows = []
    for s, f in live.items():
        for pk, g in split_periods(f, "Y").items():
            m = momentum_matrix(g)
            rows.append({"yıl": pk, "coin": s,
                         "4h→8h %": m[("4h", "8h")].mean() * 100,
                         "1d→1d %": m[("1d", "1d")].mean() * 100,
                         "5d→5d %": m[("5d", "5d")].mean() * 100})
    e = pd.DataFrame(rows)
    g = e.groupby("yıl")
    out = pd.DataFrame({"n_coin": g["coin"].count()})
    for c in ("4h→8h %", "1d→1d %", "5d→5d %"):
        out[f"{c} ort"] = g[c].mean()
        out[f"{c} coin+"] = g[c].apply(lambda x: int((x > 0).sum()))
    print(fmt(out.reset_index(), md))

    print(h("F. UZUN-YALNIZ, MALİYET SONRASI — r_L>0 iken r_H ortalaması − 0.15% gidiş-dönüş; 23 coin, 665g"))
    rows = []
    for lname, L in MOM_L:
        for hname, H in MOM_H:
            allv, per = [], []
            for s_, f in uni.items():
                lc = np.log(f["close"].to_numpy(float))
                idx = np.arange(L, len(lc) - H, H)
                rL = lc[idx] - lc[idx - L]
                rH = lc[idx + H] - lc[idx]
                v = rH[rL > 0] - 2 * COST
                allv.append(v)
                per.append(v.mean())
            v = np.concatenate(allv)
            per_arr = np.array(per)
            rows.append({"L": lname, "H": hname, "net_%/işlem": v.mean() * 100,
                         "t": v.mean() / (v.std(ddof=1) / np.sqrt(len(v))),
                         "coin+": int((per_arr > 0).sum()), "n": len(v)})
    print(fmt(pd.DataFrame(rows), md))


if __name__ == "__main__":
    main()
