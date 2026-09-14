"""Comparison table across synthetic scenarios, next to the real-data references.

    python3.12 experiments/synth/report.py            # table
    python3.12 experiments/synth/report.py --md       # markdown for DEFTER.md

Reads experiments/synth/results/*.json. The real rows come from the ledger's
recorded arms for the deployed geometry (R1-sl225t96 == today's config.py) and
the live track record on breakoutbot.dev.
"""
from __future__ import annotations

import glob
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RES  = os.path.join(ROOT, "experiments", "synth", "results")
REAL = {  # arm file → label
    "N1-control-20260910_240d.json": "GERÇEK 240g (mevcut geometri)",
    "R1-sl225t96_665d.json":         "GERÇEK 665g (mevcut geometri, restart)",
}
ORDER = ["null", "null_mart", "trend", "trend_slow", "chop", "bull", "bear", "bootstrap",
         "2027", "2028", "2029", "2030"]


def _allin_real(d: dict) -> tuple[float | None, int]:
    ps = d.get("pos_stats") or {}
    by = (ps.get("by_sleeve") or {}).get("MOMENTUM")
    if not by or not by.get("n"):
        return None, 0
    fees = sum(leg["pnl"] for leg in d.get("legs", [])
               if leg.get("exit_type") == "OPEN" and leg.get("sleeve") == "MOMENTUM")
    return (by["pnl"] + fees) / by["n"] / 10.0, by["n"]


def real_rows() -> list[dict]:
    out = []
    for fn, label in REAL.items():
        p = os.path.join(ROOT, "experiments", "results", fn)
        if not os.path.exists(p):
            continue
        d = json.load(open(p))
        r, n = _allin_real(d)
        t = d.get("tally") or {}
        out.append({"label": label, "seeds": 1, "days": d["days"],
                    "fb_mean": d["final_balance"], "fb_sd": float("nan"),
                    "fb_min": d["final_balance"], "fb_max": d["final_balance"],
                    "p_profit": float(d["final_balance"] > d.get("start_balance", 1000)),
                    "dd_mean": d["max_dd"], "dd_worst": d["max_dd"],
                    "halts": d.get("n_halts") or 0, "n_mom": n, "mom_r": r,
                    "exits": {k: t.get(k, 0) for k in ("TP2", "SL", "TRAIL", "TIMEOUT")},
                    "vr": None})
    return out


def synth_rows() -> list[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(RES, "*.json"))):
        d = json.load(open(p))
        rows = d.get("rows") or []
        if not rows:
            continue
        fb = np.array([x["final_balance"] for x in rows])
        dd = np.array([x["max_dd"] for x in rows])
        mr = np.array([x["allin"].get("MOMENTUM", {}).get("allin_r", np.nan) for x in rows])
        nm = np.array([x["allin"].get("MOMENTUM", {}).get("n", 0) for x in rows])
        ex = {k: float(np.mean([x["tally"][k] for x in rows])) for k in ("TP2", "SL", "TRAIL", "TIMEOUT")}
        m  = rows[0]["market"]["ADAUSDT"]
        # pooled all-in R across seeds (weights by n) and its SE from per-seed spread
        pooled = float(np.nansum(mr * nm) / max(nm.sum(), 1))
        stem = os.path.basename(p)[:-5]
        base = f"{d['scenario']}_{d['days']}d"
        tag = stem[len(base) + 1:] if stem.startswith(base + "_") else ""
        out.append({"label": f"{d['scenario']} {d['days']}g" + (" (restart)" if d.get("restarts") else "")
                    + (f" [{tag}]" if tag else ""),
                    "scenario": d["scenario"], "seeds": len(rows), "days": d["days"],
                    "fb_mean": fb.mean(), "fb_sd": fb.std(ddof=1) if len(fb) > 1 else float("nan"),
                    "fb_min": fb.min(), "fb_max": fb.max(),
                    "p_profit": float(np.mean(fb > 1000)),
                    "dd_mean": dd.mean(), "dd_worst": dd.min(),
                    "halts": int(sum(x["n_halts"] for x in rows)),
                    "n_mom": int(nm.mean()), "mom_r": pooled,
                    "mom_r_se": float(np.nanstd(mr, ddof=1) / math.sqrt(len(rows))) if len(rows) > 1 else float("nan"),
                    "exits": ex,
                    "vr": f"{m['VR_4h']}/{m['VR_1d']}/{m['VR_5d']}"})
    key = {s: i for i, s in enumerate(ORDER)}
    out.sort(key=lambda r: (key.get(r["scenario"], 99), r["days"]))
    return out


def fmt(rows: list[dict]) -> str:
    h = (f"  {'senaryo':<40} {'n':>2} {'gün':>4} {'bakiye μ':>9} {'±sd':>7} {'min':>8} {'max':>8} "
         f"{'P(kâr)':>6} {'DD μ':>7} {'DD kötü':>8} {'halt':>4} {'mom n':>5} {'momR':>7} {'TP2/SL/TRL/TMO':>16} {'VR 4h/1d/5d':>14}")
    lines = [h, "  " + "-" * (len(h) - 2)]
    for r in rows:
        sd = "" if math.isnan(r["fb_sd"]) else f"{r['fb_sd']:7.0f}"
        mr = "   —   " if r["mom_r"] is None or (isinstance(r["mom_r"], float) and math.isnan(r["mom_r"])) else f"{r['mom_r']:+7.3f}"
        ex = r["exits"]
        lines.append(
            f"  {r['label']:<40} {r['seeds']:>2} {r['days']:>4} {r['fb_mean']:9.0f} {sd:>7} {r['fb_min']:8.0f} {r['fb_max']:8.0f} "
            f"{r['p_profit']:6.0%} {r['dd_mean']:6.1f}% {r['dd_worst']:7.1f}% {r['halts']:>4} {r['n_mom']:>5} {mr} "
            f"{ex['TP2']:4.0f}/{ex['SL']:3.0f}/{ex['TRAIL']:3.0f}/{ex['TIMEOUT']:3.0f}  {r['vr'] or '':>14}")
    return "\n".join(lines)


def fmt_md(rows: list[dict]) -> str:
    lines = ["| senaryo | tohum | gün | bakiye μ ± sd | min / max | P(kâr) | DD μ / en kötü | halt | mom n | momR all-in | TP2/SL/TRL/TMO | VR 4h/1d/5d |",
             "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for r in rows:
        sd = "" if math.isnan(r["fb_sd"]) else f" ± {r['fb_sd']:.0f}"
        mr = "—" if r["mom_r"] is None or (isinstance(r["mom_r"], float) and math.isnan(r["mom_r"])) else f"{r['mom_r']:+.3f}"
        se = f" ± {r['mom_r_se']:.3f}" if r.get("mom_r_se") and not math.isnan(r["mom_r_se"]) else ""
        ex = r["exits"]
        lines.append(f"| {r['label']} | {r['seeds']} | {r['days']} | ${r['fb_mean']:.0f}{sd} | {r['fb_min']:.0f} / {r['fb_max']:.0f} | "
                     f"{r['p_profit']:.0%} | {r['dd_mean']:.1f}% / {r['dd_worst']:.1f}% | {r['halts']} | {r['n_mom']} | **{mr}**{se} | "
                     f"{ex['TP2']:.0f}/{ex['SL']:.0f}/{ex['TRAIL']:.0f}/{ex['TIMEOUT']:.0f} | {r['vr'] or ''} |")
    return "\n".join(lines)


if __name__ == "__main__":
    rows = synth_rows() + real_rows()
    print(fmt_md(rows) if "--md" in sys.argv else fmt(rows))
