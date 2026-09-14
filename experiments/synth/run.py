"""Run the deployed portfolio simulator on synthetic markets.

    python3.12 experiments/synth/run.py --scenario null  --days 240 --seeds 8
    python3.12 experiments/synth/run.py --scenario trend --days 240 --seeds 8
    python3.12 experiments/synth/run.py --scenario 2027  --days 365 --seeds 8 --restarts
    python3.12 experiments/synth/run.py --scenario bootstrap --days 240 --seeds 8

Scenarios: the keys of gen.SCENARIOS, the keys of gen.YEAR_MIX (Markov regime
years), and `bootstrap` (re-ordered real days). Every seed is one full
backtest.run_portfolio replay — the live-parity engine, untouched — so a result
here is directly comparable to the ledger's real-data arms. One 240d replay is
~14 CPU-minutes; seeds run in parallel (--jobs).

Results: experiments/synth/results/<scenario>_<days>d.json, one entry per seed,
with the market's measured variance ratios next to the account's outcome, so a
number can never be separated from the market that produced it.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RES  = os.path.join(ROOT, "experiments", "synth", "results")
LOGS = os.path.join(ROOT, "experiments", "synth", "logs")
RISK = {"MOMENTUM": 10.0, "MR": 5.0, "PROBE": 20.0}


def build_market(scenario: str, days: int, seed: int, block_days: int = 5,
                 drift: float = 0.0, source_days: int = 240,
                 before: str | None = None, after: str | None = None) -> tuple[dict, dict]:
    """→ (frames, market_meta)."""
    from experiments.synth import gen
    meta: dict = {"scenario": scenario, "seed": seed}
    if scenario in gen.SCENARIOS:
        frames = gen.simulate(gen.SCENARIOS[scenario], days, seed)
        meta["law"] = gen.SCENARIOS[scenario].__dict__
    elif scenario in gen.YEAR_MIX:
        frames, seq = gen.simulate_regime(gen.YEAR_MIX[scenario], days, seed)
        warm = gen.REGIME_WARMUP_BARS // gen.BARS_PER_DAY
        traded = seq[warm:]
        meta["mix_target"]   = gen.YEAR_MIX[scenario]
        meta["mix_realised"] = {s: round(traded.count(s) / len(traded), 2)
                                for s in gen.REGIME_STATES}
        meta["states"] = {k: v.__dict__ for k, v in gen.REGIME_STATES.items()}
    elif scenario == "bootstrap":
        real = gen.load_real(source_days, before=before, after=after)
        n_src = len(next(iter(real.values()))) // gen.BARS_PER_DAY
        frames = gen.bootstrap(real, days, seed, block_days=block_days, drift_ann=drift)
        meta["block_days"] = block_days
        meta["drift_ann"]  = drift
        meta["source"]     = (f"backtests/data/*_{source_days}d+40w.pkl"
                              + (f" before {before}" if before else "")
                              + (f" after {after}" if after else "")
                              + f" ({n_src} source days)")
    else:
        raise SystemExit(f"unknown scenario {scenario!r}")
    meta["market"] = {s: gen.describe(frames, s) for s in ("ADAUSDT", "BTCUSDT")}
    return frames, meta


def _allin(r: dict) -> dict:
    """ledger.py's all-in momentum R: sleeve pnl + its OPEN-leg entry fees."""
    ps = r.get("pos_stats") or {}
    by = ps.get("by_sleeve") or {}
    fees: dict[str, float] = {}
    for leg in r.get("_legs", []):
        if leg.get("exit_type") == "OPEN":
            fees[leg["sleeve"]] = fees.get(leg["sleeve"], 0.0) + leg["pnl"]
    out = {}
    for sleeve in ("MOMENTUM", "MR"):
        v = by.get(sleeve)
        if v and v.get("n"):
            out[sleeve] = {"n": v["n"],
                           "allin_r": round((v["pnl"] + fees.get(sleeve, 0.0)) / v["n"] / RISK[sleeve], 3),
                           "pnl": round(v["pnl"] + fees.get(sleeve, 0.0), 2)}
    return out


def run_one(scenario: str, days: int, seed: int, restarts: bool,
            block_days: int, drift: float, source_days: int = 240,
            before: str | None = None, after: str | None = None,
            random_entry: float = 0.0) -> dict:
    from config import INITIAL_BALANCE, REGIME_WARMUP_BARS, TOKENS
    import backtest
    backtest._RESTARTS = restarts
    if random_entry > 0:
        from experiments.synth.random_entry import install
        install(random_entry, seed)

    os.makedirs(LOGS, exist_ok=True)
    log = os.path.join(LOGS, f"{scenario}_{days}d_s{seed}.log")
    t0 = time.time()
    frames, meta = build_market(scenario, days, seed, block_days, drift,
                                source_days, before, after)
    tokens = list(TOKENS)
    with open(log, "w") as fh, contextlib.redirect_stdout(fh):
        r = backtest.run_portfolio(tokens, days, INITIAL_BALANCE,
                                   data={t: frames[t].copy() for t in tokens},
                                   btc_df=frames["BTCUSDT"].copy(),
                                   trade_start_idx=REGIME_WARMUP_BARS)
    ps = r["pos_stats"]
    t  = r["tally"]
    per_sym: dict[str, float] = {}
    for p in r.get("_positions", []):
        per_sym[p.symbol] = round(per_sym.get(p.symbol, 0.0) + p.pnl, 2)
    return {
        **meta,
        "days":          days,
        "restarts":      restarts,
        "random_entry":  random_entry or None,
        "final_balance": round(r["final_balance"], 2),
        "total_return":  round(r["total_return"], 2),
        "max_dd":        round(r["max_dd"], 2),
        "halted":        r["halted"],
        "n_halts":       r["n_halts"],
        "days_run":      round(r["days_run"], 1),
        "n_positions":   ps["n_positions"],
        "win_rate":      ps["win_rate"],
        "payoff":        ps["payoff"],
        "expectancy":    ps["expectancy"],
        "expectancy_r":  ps["expectancy_r"],
        "profit_factor": ps["profit_factor"],
        "entry_fees":    round(r["entry_fees"], 2),
        "allin":         _allin(r),
        "tally":         {k: (round(v, 2) if isinstance(v, float) else v) for k, v in t.items()},
        "regime_counts": r["regime_counts"],
        "per_symbol":    per_sym,
        "recon_err":     round(r["recon_err"], 4),
        "cpu_min":       round((time.time() - t0) / 60, 1),
    }


def summarise(rows: list[dict]) -> str:
    import numpy as np
    if not rows:
        return "(no rows)"
    fb  = np.array([x["final_balance"] for x in rows])
    dd  = np.array([x["max_dd"] for x in rows])
    n   = np.array([x["n_positions"] for x in rows])
    er  = np.array([x["expectancy_r"] if x["expectancy_r"] is not None else np.nan for x in rows])
    ar  = np.array([x["allin"].get("MOMENTUM", {}).get("allin_r", np.nan) for x in rows])
    fees = np.array([x["entry_fees"] for x in rows])
    halts = np.array([x["n_halts"] for x in rows])
    lines = [
        f"  seeds {len(rows)}   days {rows[0]['days']}   restarts={rows[0]['restarts']}",
        f"  final balance : mean ${fb.mean():.2f}  sd ${fb.std(ddof=1) if len(fb) > 1 else 0:.2f}  "
        f"min ${fb.min():.2f}  max ${fb.max():.2f}   P(profit) {np.mean(fb > 1000):.0%}",
        f"  max drawdown  : mean {dd.mean():.1f}%  worst {dd.min():.1f}%   hard stops {halts.sum()} total",
        f"  positions     : mean {n.mean():.0f}  (min {n.min()}, max {n.max()})",
        f"  expectancy/pos: {np.nanmean(er):+.3f} R (exit-only)   all-in mom {np.nanmean(ar):+.3f} R",
        f"  entry fees    : mean ${fees.mean():.2f} per run",
    ]
    ex = {k: np.mean([x["tally"][k] for x in rows]) for k in ("TP1", "TP2", "SL", "TRAIL", "TIMEOUT", "probe", "full")}
    lines.append("  exits (mean)  : " + "  ".join(f"{k}={v:.0f}" for k, v in ex.items()))
    m = rows[0]["market"]["ADAUSDT"]
    lines.append(f"  market (ADA, seed {rows[0]['seed']}): VR 4h {m['VR_4h']} · 8h {m['VR_8h']} · 1d {m['VR_1d']} · 5d {m['VR_5d']}")
    if "mix_realised" in rows[0]:
        lines.append("  regime mix    : " + "  ".join(f"s{x['seed']}={x['mix_realised']}" for x in rows))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--days", type=int, default=240)
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--seed0", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--restarts", action="store_true",
                    help="continue past PEAK_DD_LIMIT hard stops (BT_RESTARTS=1); "
                         "required for any window a hard stop would truncate")
    ap.add_argument("--block-days", type=int, default=5, help="bootstrap block length")
    ap.add_argument("--drift", type=float, default=0.0, help="bootstrap annualised drift overlay")
    ap.add_argument("--source-days", type=int, default=240,
                    help="bootstrap: which cached window to draw real days from (240 or 665)")
    ap.add_argument("--before", default=None, help="bootstrap: only source days before this UTC date")
    ap.add_argument("--after",  default=None, help="bootstrap: only source days on/after this UTC date")
    ap.add_argument("--tag", default="", help="suffix for the results file")
    ap.add_argument("--random-entry", type=float, default=0.0, metavar="P",
                    help="research: replace the signal engine with a coin flip (STRONG_LONG "
                         "with prob P per idle bar), always confirm, lift the regime gate — "
                         "a pure test of the exit geometry (see random_entry.py)")
    a = ap.parse_args()

    os.makedirs(RES, exist_ok=True)
    out = os.path.join(RES, f"{a.scenario}_{a.days}d{('_' + a.tag) if a.tag else ''}.json")
    seeds = list(range(a.seed0, a.seed0 + a.seeds))
    # X_* overrides reach the workers through the environment (config.py reads
    # them at import), so an exit-geometry arm on a synthetic market is just
    #   X_SL_FULL_ATR=8 X_TIMEOUT_BARS=288 python3.12 experiments/synth/run.py --scenario trend --tag wide
    env = {k: v for k, v in os.environ.items() if k.startswith("X_") or k.startswith("BT_")}
    print(f"▶ {a.scenario}: {len(seeds)} seeds × {a.days}d, {a.jobs} jobs → {out}"
          + (f"   env {env}" if env else "")
          + (f"   RANDOM ENTRY p={a.random_entry:g} (no regime gate)" if a.random_entry else ""), flush=True)
    rows: list[dict] = []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(run_one, a.scenario, a.days, s, a.restarts, a.block_days, a.drift,
                          a.source_days, a.before, a.after, a.random_entry): s
                for s in seeds}
        for f in as_completed(futs):
            s = futs[f]
            try:
                r = f.result()
            except Exception as exc:          # one bad seed must not sink the batch
                print(f"  ✗ seed {s}: {exc!r}", flush=True)
                continue
            rows.append(r)
            print(f"  ✓ seed {s:>3}  ${r['final_balance']:>8.2f}  dd {r['max_dd']:6.1f}%  "
                  f"pos {r['n_positions']:>4}  momR {r['allin'].get('MOMENTUM', {}).get('allin_r', float('nan')):+.3f}  "
                  f"halts {r['n_halts']}  ({r['cpu_min']} min)  [{(time.time() - t0) / 60:.0f} min elapsed]",
                  flush=True)
            rows.sort(key=lambda x: x["seed"])
            with open(out, "w") as fh:           # checkpoint after every seed
                json.dump({"scenario": a.scenario, "days": a.days, "restarts": a.restarts,
                           "env": env, "rows": rows}, fh, indent=1)
    print(f"\n{a.scenario} — {summarise(rows)}")
    print(f"  saved → {out}")


if __name__ == "__main__":
    main()
