"""Audit saved portfolio legs and compare N2 with the adopted R1 geometry.

Run with python3.12; no replay or network request is needed.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from collections import Counter, defaultdict
from itertools import groupby
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
COST = 0.00075
ARMS = {
    240: {"baseline": "N1-control-20260910", "no_tp2": "N1-no-tp2-20260910",
          "half_tp2": "N2-tp2-half-20260911"},
    665: {"baseline": "R1-sl225t96", "no_tp2": "N1-no-tp2-20260910",
          "half_tp2": "N2-tp2-half-20260911"},
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(path):
    data = json.loads(path.read_text())
    legs = data["legs"]
    assert not data["halted"] and not data["bankrupt"]
    assert abs(data["days_run"] - data["days"]) < 0.01
    assert abs(data["recon_err"]) < 1e-8
    assert all(a["ts"] <= b["ts"] for a, b in zip(legs, legs[1:]))
    net = sum(leg["pnl"] for leg in legs)
    assert math.isclose(net, data["final_balance"] - data["start_balance"], abs_tol=1e-8)
    assert math.isclose(sum(leg["pnl"] for leg in legs if leg["exit_type"] == "OPEN"),
                        data["entry_fees"], abs_tol=1e-8)

    # Backtest records cash after each whole bar, not after each individual fill.
    cash = peak = data["start_balance"]
    drawdown = 0.0
    equity = []
    for ts, group in groupby(legs, key=lambda leg: leg["ts"]):
        cash += sum(leg["pnl"] for leg in group)
        peak = max(peak, cash)
        drawdown = min(drawdown, 100 * (cash - peak) / peak)
        equity.append({"ts": ts, "cash": cash})
    assert math.isclose(drawdown, data["max_dd"], abs_tol=1e-8)

    pending, positions = {}, []
    by_symbol = defaultdict(float)
    for leg in legs:
        by_symbol[leg["symbol"]] += leg["pnl"]
        key = (leg["symbol"], leg["sleeve"])
        if leg["exit_type"] == "OPEN":
            assert key not in pending, "A previous position is still open"
            pending[key] = {"open": leg, "exits": []}
            continue
        pos = pending[key]
        assert pos["open"]["direction"] == leg["direction"]
        assert pos["open"]["entry"] == leg["entry"]
        pos["exits"].append(leg)
        if leg["exit_type"] in {"TP1", "TP2_PARTIAL"}:
            assert len(pos["exits"]) == 1, "Repeated partial"
            continue
        opened = pos["open"]
        notional = -opened["pnl"] / COST
        exits = pos["exits"]
        assert len(exits) <= 2
        for event in exits:
            closed = notional / 2 if len(exits) == 2 else notional
            sign = 1 if opened["direction"] == "LONG" else -1
            expected = closed * (event["exit"] - opened["entry"]) / opened["entry"] * sign
            assert math.isclose(event["pnl"], expected - closed * COST, abs_tol=1e-8)
        positions.append({
            "symbol": key[0], "sleeve": key[1], "direction": opened["direction"],
            "entry_ts": opened["ts"], "exit_ts": leg["ts"], "notional": notional,
            "net": opened["pnl"] + sum(event["pnl"] for event in exits),
            "exit_types": [event["exit_type"] for event in exits],
        })
        del pending[key]
    assert not pending, "Open positions at end of window"
    counts = Counter(pos["sleeve"] for pos in positions)
    assert counts["MOMENTUM"] == data["tally"]["full"]
    assert len(positions) == data["pos_stats"]["n_positions"]
    partials = [pos for pos in positions if "TP2_PARTIAL" in pos["exit_types"]]
    assert len(partials) == data["tally"].get("TP2_PARTIAL", 0)
    return {
        "file": str(path.relative_to(ROOT)), "sha256": digest(path),
        "arm": data["_arm"], "net": net, "final_balance": cash,
        "max_drawdown_pct": drawdown, "hard_stops": data["n_halts"],
        "full_positions": counts["MOMENTUM"], "probes": counts["PROBE"],
        "partial_tp2_positions": len(partials), "all_legs": len(legs),
        "entry_cost": -data["entry_fees"], "by_symbol_net": dict(by_symbol),
        "partial_terminal_exits": dict(Counter(pos["exit_types"][-1] for pos in partials)),
        "maxopen_full_confirm_bars": data["tally"]["maxopen_block"],
        "verification": {"all_positions_closed": True, "every_exit_fee_reconciled": True,
                         "cash_and_bar_drawdown_reconciled": True},
    }, positions, equity


def main():
    output = {"windows": {}, "cache_manifest": {}, "core_matches_adopted_commit": {}}
    for name in ("strategy.py", "config.py", "metrics.py", "backtest.py"):
        original = subprocess.check_output(["git", "show", f"dded71b:{name}"], cwd=ROOT)
        current = (ROOT / name).read_bytes()
        assert current == original, f"Core changed: {name}"
        output["core_matches_adopted_commit"][name] = hashlib.sha256(current).hexdigest()
    assert (ROOT / "paper_bb.py").read_bytes() == subprocess.check_output(
        ["git", "show", "HEAD:paper_bb.py"], cwd=ROOT)
    output["live_paper_file_unchanged_from_head"] = digest(ROOT / "paper_bb.py")
    for days, arms in ARMS.items():
        suffix = "_665d" if days == 665 else ""
        manifest = ROOT / f"experiments/reports/20260910-near/cache_manifest{suffix}.json"
        recorded = json.loads(manifest.read_text())
        for record in recorded.values():
            assert digest(ROOT / record["file"]) == record["sha256"]
        output["cache_manifest"][days] = recorded
        summaries, positions = {}, {}
        for variant, arm in arms.items():
            path = ROOT / f"experiments/results/{arm}_{days}d.json"
            summaries[variant], positions[variant], _ = audit(path)
        base, half, none = (summaries[k] for k in ("baseline", "half_tp2", "no_tp2"))
        def key(pos):
            return pos["symbol"], pos["sleeve"], pos["direction"], pos["entry_ts"]
        a = {key(pos): pos for pos in positions["half_tp2"]}
        b = {key(pos): pos for pos in positions["no_tp2"]}
        shared = a.keys() & b.keys()
        summaries["comparison"] = {
            "half_minus_baseline_net": half["net"] - base["net"],
            "half_minus_baseline_dd_percentage_points": half["max_drawdown_pct"] - base["max_drawdown_pct"],
            "half_minus_no_tp2_net": half["net"] - none["net"],
            "half_beats_baseline": half["net"] > base["net"],
            "half_no_tp2_same_entry_keys": a.keys() == b.keys(),
            "half_no_tp2_changed_notionals": sum(
                not math.isclose(a[k]["notional"], b[k]["notional"], abs_tol=1e-7)
                for k in shared),
        }
        output["windows"][days] = summaries
    output["adopt"] = all(w["comparison"]["half_beats_baseline"] for w in output["windows"].values())
    (HERE / "portfolio_comparison.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"adopt": output["adopt"], "comparisons": {
        days: window["comparison"] for days, window in output["windows"].items()
    }}, indent=2))


if __name__ == "__main__":
    main()
