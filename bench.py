"""
Deterministic phase benchmark — pins the dataset so phases compare cleanly.

Each backtest run normally re-fetches "last 90 days", so the window shifts and
results drift run-to-run (a phase change gets confounded with data change). This
harness fetches SOL/INJ/FET/BTC ONCE, caches them, and every phase runs on the
SAME data — so a metric change reflects the CODE change only.

Usage:
    python bench.py faz1            # runs on cached data, prints report
    python bench.py faz1 > backtests/faz1.txt
    python bench.py faz2 > backtests/faz2.txt    # SAME data → valid A/B

To refresh the pinned window (new test period): delete backtests/data/.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

import config
from backtest import fetch_history, run_symbol, print_report
from config import INITIAL_BALANCE

# BENCH_ALL=1 → all 23 config tokens (broad-universe validation);
# otherwise the 3-coin core bench (fast phase A/B). Cache is per-symbol so the
# 23-coin set reuses the 3-coin pkls already on disk.
_bt = os.getenv("BENCH_TOKENS")
if _bt:
    TOKENS = _bt.split(",")                    # custom set, e.g. curation candidates
elif os.getenv("BENCH_ALL") == "1":
    TOKENS = list(config.TOKENS)
else:
    TOKENS = ["SOLUSDT", "INJUSDT", "FETUSDT"]
DAYS   = int(os.getenv("BENCH_DAYS", "90"))   # BENCH_DAYS=240 → deep-bear window
CACHE  = "backtests/data"


def load_data() -> dict[str, pd.DataFrame]:
    """Load pinned OHLCV from cache; fetch + cache once on first run."""
    os.makedirs(CACHE, exist_ok=True)
    out: dict[str, pd.DataFrame] = {}
    for sym in TOKENS + ["BTCUSDT"]:
        path = f"{CACHE}/{sym}_{DAYS}d.pkl"
        if os.path.exists(path):
            out[sym] = pd.read_pickle(path)
        else:
            print(f"  [cache miss] fetching {sym} {DAYS}d (one-time)…", flush=True)
            df = fetch_history(sym, DAYS, silent=True)
            df.to_pickle(path)
            out[sym] = df
    return out


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    data  = load_data()
    btc   = data["BTCUSDT"]

    results = []
    for sym in TOKENS:
        # copies so the cached frames are never mutated by precompute/regime
        r = run_symbol(sym, DAYS, INITIAL_BALANCE,
                       btc_df=btc.copy(), df=data[sym].copy())
        print_report(r)
        r.pop("_df", None)
        results.append(r)

    n_ok    = sum(1 for r in results if r["total_return"] > 0)
    avg_wr  = sum(r["win_rate"] for r in results) / len(results)
    avg_pf  = sum(r["profit_factor"] for r in results) / len(results)
    monthly = sum(r["monthly_est"] * INITIAL_BALANCE / 100 for r in results)
    worst_dd = min(r["max_dd"] for r in results)

    print(f"\n{'='*56}")
    print(f"  PHASE BENCHMARK: {label}   (pinned data, {len(results)} symbols)")
    print(f"{'─'*56}")
    print(f"  Profitable    : {n_ok}/{len(results)}")
    print(f"  Avg Win Rate  : {avg_wr:.1f}%")
    print(f"  Avg Profit Fac: {avg_pf:.2f}")
    print(f"  Worst MaxDD   : {worst_dd:+.2f}%")
    print(f"  Monthly est ($): ${monthly:+.2f}  (on ${INITIAL_BALANCE:.0f})")
    print(f"{'='*56}")
    print(f"  data: {CACHE}/ (deterministic — delete to refresh window)")


if __name__ == "__main__":
    main()
