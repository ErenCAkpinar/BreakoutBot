"""The basis trade out of sample (DEFTER Tur 13).

    python3.12 experiments/lab/basis_oos.py [--md]

Tur 7–8 ran basis.py's 54-config grid over all 5.8 years and picked the best
Sharpe on the same data; "the last two years" was a sub-period of that fit,
not a hold-out. Two honest layers here:

  walk-forward  for each test year 2022…2026 the grid is run ONLY on the bars
                before that year, the best-Sharpe config is chosen there and
                scored on the year. The five test years concatenate into one
                out-of-sample series. Parameter-free controls on the same
                slices: always-on equal-weight basis on BTC+ETH, on the
                liquid six (Tur 8's deployable set), and on all 23.
  time-OOS      the funding, spot and perp caches were extended past the
                2026-08-24 cut used by every earlier round; realised funding
                and basis drift on the unseen days since, annualised.

Panel: spot 4h (lab/spot), perp 4h (lab/perp — its own append-only cache, so
the perp leg no longer depends on the 5m backtest cache's end date), funding
summed into bars (panel.load_funding). Carry per bar per unit of notional:
spot_ret − perp_ret + funding; cost 2 × 0.075% per unit of weight change.
"""
from __future__ import annotations

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

import basis  # noqa: E402
import basis_ledger  # noqa: E402
import engine  # noqa: E402
import panel as panel_mod  # noqa: E402

TF = "4h"
PER_YEAR = engine.BARS_PER_YEAR[TF]
GRID = list(itertools.product([3, 5, 8], [42, 90, 180], [42, 180, 540], [0.0, 0.00005]))
TEST_YEARS = [2022, 2023, 2024, 2025, 2026]
# What earlier rounds had actually SEEN, per source (review 2026-09-14, finding 6):
# funding to 2026-08-28 08:00, spot to 2026-08-28 16:00 (both fetched Aug 28),
# perp 5m cache to 2026-08-24. The unseen window starts after the LATEST of
# those — the first bar after the last spot bar the earlier rounds held.
PREV_SEEN = pd.Timestamp("2026-08-28 16:00", tz="UTC")
LIQUID6 = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "ADAUSDT", "LINKUSDT", "LTCUSDT"]


def load_panel() -> basis.BasisPanel:
    syms = sorted({os.path.basename(p).rsplit("_", 1)[0] for p in glob.glob(f"experiments/lab/spot/*_{TF}.json")})
    spot_s, perp_s = {}, {}
    for s in syms:
        d = json.load(open(f"experiments/lab/spot/{s}_{TF}.json"))["bars"]
        spot_s[s] = pd.Series([b[1] for b in d], index=pd.to_datetime([b[0] for b in d], unit="ms", utc=True))
        pp = f"experiments/lab/perp/{s}_{TF}.json"
        if os.path.exists(pp):
            d = json.load(open(pp))["bars"]
            perp_s[s] = pd.Series([b[1] for b in d], index=pd.to_datetime([b[0] for b in d], unit="ms", utc=True))
    syms = [s for s in syms if s in perp_s]
    idx = None
    for s in syms:
        i = spot_s[s].index.intersection(perp_s[s].index)
        idx = i if idx is None else idx.union(i)
    assert idx is not None, "no symbol with both spot and perp data"
    idx = idx.sort_values()
    spot = np.column_stack([spot_s[s].reindex(idx).to_numpy(float) for s in syms])
    perp = np.column_stack([perp_s[s].reindex(idx).to_numpy(float) for s in syms])
    fund = panel_mod.load_funding(syms, idx, TF)
    return basis.BasisPanel(syms, idx, spot, perp, fund if fund is not None else np.zeros_like(spot))


REBAL_BARS = 180        # 30 days on 4h bars: the always-on book resets to target dollar weights monthly


def always_on(bp: basis.BasisPanel, names: list[str], rebal: int = REBAL_BARS) -> np.ndarray:
    """Equal dollar weight over `names`, re-targeted every `rebal` bars.

    In the quantity ledger a book that is NEVER re-targeted lets its notional
    ride the price: a $1 BTC basis opened in 2020 was ~$7.8 of notional by 2024
    and its funding scaled with it — a true number for that path, not a yield
    anyone can plan on. Monthly re-targeting keeps notional ≈ equity, charges
    the drift turnover, and makes the row a rate rather than a bet on the path.
    The ledger re-targets on any change of the weight vector, so the schedule
    is written as an alternating epsilon tag that changes every `rebal` bars.
    """
    cols = [bp.symbols.index(s) for s in names if s in bp.symbols]
    w = np.zeros_like(bp.spot)
    for t in range(len(bp.index)):
        ok = [c for c in cols if bp.mask[t, c]]
        if ok:
            w[t, ok] = 1.0 / len(ok) * (1.0 + 1e-9 * ((t // rebal) % 2))   # nudge marks the rebalance
    return w


def sl_stats(led: dict, sl: slice) -> dict:
    st = engine.stats(led["r"][sl], TF)
    pt = basis_ledger.parts(led, sl, PER_YEAR)
    return {"net_%": pt["net"], "funding_%": pt["funding"], "basis_%": pt["basis_drift"], "cost_%": pt["cost"],
            "ann_%": st["ann_ret"] * 100, "vol_%": st["ann_vol"] * 100, "DD_%": st["max_dd"] * 100, "SR": st["sharpe"],
            "n": int(sl.stop - sl.start)}


def main() -> None:
    md = "--md" in sys.argv
    bp = load_panel()
    n = len(bp.index)
    print(f"# {bp}", file=sys.stderr)

    # Grid selection uses the fast marker (basis.evaluate) — it only has to RANK
    # configs on the training slice. Everything that is reported is re-run
    # through the quantity ledger.
    grid_r, grid_w = {}, {}
    for cfg in GRID:
        w = basis.weights(bp, *cfg)
        grid_w[cfg] = w
        grid_r[cfg] = basis.evaluate(bp, w)
    controls = {"BTC+ETH hep açık": always_on(bp, ["BTCUSDT", "ETHUSDT"]),
                "likit-6 hep açık": always_on(bp, LIQUID6),
                "23 hep açık": always_on(bp, bp.symbols)}
    ctrl_led = {k: basis_ledger.run(bp, w) for k, w in controls.items()}

    # Walk-forward as ONE continuous weight path: the year-boundary switch is a
    # real transition the ledger charges (finding 7), not a splice.
    rows = []
    wf_w = np.zeros_like(bp.spot)
    chosen = {}
    for y in TEST_YEARS:
        t0 = int(np.searchsorted(bp.index, pd.Timestamp(f"{y}-01-01", tz="UTC")))
        t1 = min(int(np.searchsorted(bp.index, pd.Timestamp(f"{y + 1}-01-01", tz="UTC"))), n)
        train = slice(0, t0)
        best = max(GRID, key=lambda c: engine.stats(grid_r[c][train], TF)["sharpe"])
        chosen[y] = (best, t0, t1)
        wf_w[t0:t1] = grid_w[best][t0:t1]
    wf_led = basis_ledger.run(bp, wf_w)
    for y, (best, t0, t1) in chosen.items():
        test = slice(t0, t1)
        st = sl_stats(wf_led, test)
        st_in = engine.stats(grid_r[best][:t0], TF)
        rows.append({"test yılı": y, "seçilen (top,lb,reb,minF)": f"{best[0]},{best[1]},{best[2]},{best[3]*100:.3f}%",
                     "eğitim SR": st_in["sharpe"], "eğitim yıllık %": st_in["ann_ret"] * 100,
                     **{f"OOS {k}": v for k, v in st.items()},
                     **{f"{k} net %": sl_stats(ctrl_led[k], test)["net_%"] for k in controls}})
    wf = pd.DataFrame(rows)

    t0 = int(np.searchsorted(bp.index, pd.Timestamp("2022-01-01", tz="UTC")))
    span = slice(t0, n)
    summary = {"walk-forward OOS 2022→": sl_stats(wf_led, span)}
    for k in controls:
        summary[f"{k} 2022→"] = sl_stats(ctrl_led[k], span)
    best_all = max(GRID, key=lambda c: engine.stats(grid_r[c], TF)["sharpe"])
    summary["in-sample en iyi SR (tüm veri seçimi) 2022→"] = {**sl_stats(basis_ledger.run(bp, grid_w[best_all]), span), "cfg": best_all}
    summ = pd.DataFrame([{"seri": k, "yıllık %": v["ann_%"], "vol %": v["vol_%"], "DD %": v["DD_%"], "SR": v["SR"],
                          "funding %/y": v["funding_%"], "basis %/y": v["basis_%"], "maliyet %/y": v["cost_%"],
                          "net %/y": v["net_%"], "cfg": str(v.get("cfg", ""))} for k, v in summary.items()])

    # time-OOS: only bars strictly after what earlier rounds had seen; closed bars only
    c0 = int(np.searchsorted(bp.index, PREV_SEEN, side="right"))
    days = (bp.index[-1] - bp.index[c0 - 1]).total_seconds() / 86400
    trows = []
    fund = np.nan_to_num(bp.funding[c0:])
    drift = (bp.spot_ret - bp.perp_ret)[c0:]
    for label, names in (("BTC", ["BTCUSDT"]), ("ETH", ["ETHUSDT"]), ("likit-6 ort", LIQUID6), ("23 medyan", bp.symbols)):
        cols = [bp.symbols.index(s_) for s_ in names]
        f_ann = fund[:, cols].sum(axis=0) / days * 365 * 100
        d_ann = drift[:, cols].sum(axis=0) / days * 365 * 100
        agg = np.median if label.endswith("medyan") else np.mean
        trows.append({"set": label, "gün": round(days, 1), "funding yıllık %": float(agg(f_ann)),
                      "basis kayması yıllık %": float(agg(d_ann)), "brüt yıllık %": float(agg(f_ann + d_ann)),
                      "pozitif coin": f"{int((f_ann > 0).sum())}/{len(cols)}"})
    tos = pd.DataFrame(trows)

    def out(df: pd.DataFrame, title: str) -> None:
        print(f"\n### {title}\n" if md else f"\n══ {title} ══")
        if md:
            cols = list(df.columns)
            print("| " + " | ".join(cols) + " |")
            print("|" + "|".join("--:" for _ in cols) + "|")
            for _, r in df.iterrows():
                print("| " + " | ".join(("" if isinstance(v, float) and np.isnan(v) else (f"{v:.2f}" if isinstance(v, float) else str(v))) for v in r) + " |")
        else:
            pd.set_option("display.width", 250)
            print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    out(wf, "A. Walk-forward: her test yılı için konfig yalnız önceki veride seçildi (en iyi Sharpe)")
    out(summ, "B. 2022-01 → veri sonu: birleşik OOS serisi vs parametresiz kontroller vs in-sample seçim")
    out(tos, f"C. Zaman-OOS: {bp.index[c0]:%Y-%m-%d %H:%M} → {bp.index[-1]:%Y-%m-%d %H:%M} (önceki turların görmediği, kapanmış mumlar), yıllıklandırılmış")


if __name__ == "__main__":
    main()
