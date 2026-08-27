"""Read every recorded arm and print the comparison table.

    python3.12 experiments/ledger.py            # all arms, all windows
    python3.12 experiments/ledger.py 240        # one window

WHY THE ALL-IN COLUMN IS THE ONE THAT DECIDES
---------------------------------------------
`pos_stats` expectancy omits entry fees: aggregate_positions() drops "OPEN" legs,
and an exit leg carries only its own side's cost. On a ~$900 notional that is
~$0.68 per position, ~0.07R — enough to flip a marginal arm's sign. Every figure
below charges each sleeve its own entry fees before scoring.

DECISION RULE (from BENCHMARKS.md Faz 7)
----------------------------------------
An arm is adopted only if it beats the baseline on **both** windows. One window is
not evidence: per-position R has SD ~1.7R, so at n~120 the standard error on a
window's mean is ~0.16R — a 0.05R "improvement" is noise wearing a number.

WHICH NUMBER DECIDES
--------------------
The **account's total return**, not the momentum sleeve's R. Momentum R is the
right diagnostic for an arm that changes how momentum trades, but it is blind to
an arm that changes something else: `R1-mr-off` scored Δ+0.000R on momentum —
correct, and useless, because the whole point of that arm is the MR sleeve it
removes. The account return is the one figure every arm moves through, whichever
sleeve it touches, and it already carries all fees. momR stays in the table as
the diagnostic that says *where* a change came from.
"""
from __future__ import annotations

import glob
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "experiments", "results")
RISK = {"MOMENTUM": 10.0, "MR": 5.0, "PROBE": 20.0}


def load(path: str) -> dict:
    d = json.load(open(path))
    ps = d.get("pos_stats") or {}
    by = ps.get("by_sleeve") or {}
    fees: dict[str, float] = {}
    for leg in d.get("legs", []):
        if leg.get("exit_type") == "OPEN":
            fees[leg["sleeve"]] = fees.get(leg["sleeve"], 0.0) + leg["pnl"]

    def allin(sleeve: str):
        v = by.get(sleeve)
        if not v or not v["n"]:
            return None, 0
        return (v["pnl"] + fees.get(sleeve, 0.0)) / v["n"] / RISK[sleeve], v["n"]

    mom_r, mom_n = allin("MOMENTUM")
    mr_r, mr_n = allin("MR")
    t = d.get("tally") or {}
    arm = d.get("_arm") or {}
    # SE of the window's mean momentum R. Printed next to the mean so an arm is
    # never compared without the width of the number it is being compared on.
    se = 1.7 / math.sqrt(mom_n) if mom_n else None
    return {
        "arm": arm.get("id", "?"), "days": arm.get("days", d.get("days")),
        "env": " ".join(arm.get("env", [])) or "—",
        "mom_r": mom_r, "mom_n": mom_n, "se": se,
        "mr_r": mr_r, "mr_n": mr_n,
        "bal": d.get("final_balance"), "dd": d.get("max_dd"),
        "pf": ps.get("profit_factor"), "wr": ps.get("win_rate"),
        "fees": sum(fees.values()),
        "tp2": t.get("TP2"), "sl": t.get("SL"),
        "trail": t.get("TRAIL"), "tmo": t.get("TIMEOUT"),
        "halted": d.get("halted"), "n_halts": d.get("n_halts", 0),
        "days_run": d.get("days_run"),
    }


def main() -> None:
    want = sys.argv[1] if len(sys.argv) > 1 else None
    rows = [load(p) for p in sorted(glob.glob(os.path.join(RES, "*.json")))]
    if want:
        rows = [r for r in rows if str(r["days"]) == want]
    if not rows:
        print("kayıt yok — önce experiments/run_arm.sh çalıştır")
        return

    for days in sorted({r["days"] for r in rows}):
        sub = sorted([r for r in rows if r["days"] == days], key=lambda r: r["arm"])
        print(f"\n{'='*104}")
        print(f"  PENCERE: {days} gün   ·   5 coin, tek hesap, MAX_OPEN=2")
        print(f"{'-'*104}")
        print(f"  {'arm':<22} {'momR':>8} {'±SE':>6} {'n':>4} {'mrR':>7} "
              f"{'PF':>5} {'bakiye':>10} {'DD':>7} {'ücret':>8}  TP2/SL/TRL/TMO")
        base = next((r for r in sub if r["arm"].endswith("baseline")), None)
        for r in sub:
            d_str = ""
            if base and r is not base and r["mom_r"] is not None and base["mom_r"] is not None:
                d = r["mom_r"] - base["mom_r"]
                # Two-sample SE against the baseline arm on the same window.
                sed = math.sqrt((r["se"] or 0) ** 2 + (base["se"] or 0) ** 2)
                sig = "✓" if abs(d) > 2 * sed else "~"
                d_str = f"  Δ{d:+.3f}{sig}"
            mr = f"{r['mr_r']:+.3f}" if r["mr_r"] is not None else "kapalı"
            halt = "  🛑HALT" if r["halted"] else ""
            print(f"  {r['arm']:<22} {r['mom_r']:>+8.3f} {r['se']:>6.3f} {r['mom_n']:>4} "
                  f"{mr:>7} {r['pf']:>5.2f} {r['bal']:>10.2f} {r['dd']:>+6.2f}% "
                  f"{r['fees']:>8.2f}  {r['tp2']:>3}/{r['sl']:>3}/{r['trail']:>3}/{r['tmo']:>3}"
                  f"{d_str}{halt}")
        print(f"{'-'*104}")
        print("  Δ = baseline'a göre momentum all-in R farkı (TEŞHİS — nereden geldiğini söyler).")
        print("      ✓ = |Δ| > 2×SE (gürültüden ayrılıyor)   ~ = gürültü içinde, TEK BAŞINA KANIT DEĞİL")
        print("  KARAR metriği hesap getirisidir (aşağıdaki tablo): her kol hangi sleeve'e")
        print("  dokunursa dokunsun oradan geçer. MR'ı kapatan bir kol momR'de Δ=0 gösterir.")

    # Cross-window verdict: the only rule that adopts anything.
    arms = sorted({r["arm"] for r in rows})
    wins = sorted({r["days"] for r in rows})
    if len(wins) > 1:
        print(f"\n{'='*104}")
        print("  KARAR TABLOSU — bir arm ancak HER İKİ pencerede de baseline'ı geçerse alınır")
        print(f"{'-'*104}")
        print(f"  {'arm':<22} " + " ".join(f"{str(w)+'g':>12}" for w in wins) + "   karar")
        for a in arms:
            if a.endswith("baseline"):
                continue
            cells: list[str] = []
            oks: list[bool | None] = []
            for w in wins:
                r = next((x for x in rows
                          if x["arm"] == a and x["days"] == w), {})
                b = next((x for x in rows
                          if x["arm"].endswith("baseline") and x["days"] == w), {})
                if not r or not b or r["bal"] is None or b["bal"] is None:
                    cells.append(f"{'—':>12}")
                    oks.append(None)
                    continue
                # A halted run stopped mid-window. Two arms that replayed
                # different amounts of history have no comparable final balance —
                # the "better" one may simply have survived longer. Refuse the
                # comparison rather than print a number that reads like one.
                if r["halted"] or b["halted"]:
                    cells.append(f"{'KESİK':>12}")
                    oks.append(None)
                    continue
                # Account dollars: the one metric every arm moves, whichever
                # sleeve it touches, and it already has all fees in it.
                d = r["bal"] - b["bal"]
                cells.append(f"{d:>+11.2f}$")
                oks.append(d > 0)
            if None in oks:
                v = "EKSİK — pencere koşulmadı"
            elif all(oks):
                v = "✅ AL"
            elif any(oks):
                v = "❌ RED — tek pencerede iyi, diğerinde değil"
            else:
                v = "❌ RED"
            print(f"  {a:<22} " + " ".join(cells) + f"   {v}")
        print(f"{'='*104}")


if __name__ == "__main__":
    main()
