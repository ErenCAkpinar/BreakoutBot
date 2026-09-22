"""Deterministic replay of the LIVE engine — a behaviour-equivalence harness.

Why this exists: paper_bb.py is the code running the account, and it had no way
to prove that a change to it changed nothing. The backtest has that property
(same window, same dump), the live engine did not. This gives it one.

    python3.12 experiments/replay_paper.py before.json 3000
    …make the change…
    python3.12 experiments/replay_paper.py after.json 3000
    python3.12 -c "import json;print(json.load(open('before.json'))==json.load(open('after.json')))"

A True there means: same trades, same balance, same funnel counters, same log
lines (minus the wall-clock stamp), bar for bar. It is what proved the
2026-09-22 refactor of _process_bar was a no-op.

Nothing here touches the network or the real state file: cwd is a throwaway
directory, every fetch is served from the cached windows in backtests/data
(this project's own frames, written by its backtest), and the clock is pinned
to the replayed bar so _refresh_regimes fires on the same bars every run.

Requires the 240d+40w cache. Regenerate it with:
    python3.12 backtest.py --days 240 --cache
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

import pandas as pd

ROOT = str(pathlib.Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT)

OUT_PATH = os.path.abspath(sys.argv[1])
N_BARS = int(sys.argv[2]) if len(sys.argv) > 2 else 1500

TOKENS = ["UNIUSDT", "INJUSDT", "ADAUSDT", "NEARUSDT"]
FRAME_SYMBOLS = TOKENS + ["BTCUSDT"]

# ── Load cached 5m frames, aligned on a shared timestamp grid ────────────────
frames: dict[str, pd.DataFrame] = {}
for sym in FRAME_SYMBOLS:
    df = pd.read_pickle(f"{ROOT}/backtests/data/{sym}_240d+40w.pkl")
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    frames[sym] = df

common = set(frames[FRAME_SYMBOLS[0]]["ts"])
for sym in FRAME_SYMBOLS[1:]:
    common &= set(frames[sym]["ts"])
grid = sorted(common)

# Start far enough in that every symbol has a full look-back window.
START = 12_000
assert START + N_BARS < len(grid), f"grid too short: {len(grid)}"

by_ts = {sym: df.set_index("ts", drop=False) for sym, df in frames.items()}

_now_ms = grid[START]


def _frame_up_to(sym: str, ts: int, bars: int) -> pd.DataFrame:
    df = by_ts[sym]
    sub = df.loc[:ts]
    return sub.tail(bars).reset_index(drop=True)


def fake_fetch_recent(symbol: str, bars: int = 200) -> pd.DataFrame:
    return _frame_up_to(symbol, _now_ms, bars)


def fake_fetch_4h(symbol: str, bars: int = 260) -> pd.DataFrame:
    """Resample the cached 5m frame to closed 4h candles."""
    sub = _frame_up_to(symbol, _now_ms, 80_000)
    idx = pd.to_datetime(sub["ts"], unit="ms", utc=True)
    o = sub.set_index(idx)
    agg = o.resample("4h").agg({"open": "first", "high": "max", "low": "min",
                               "close": "last", "volume": "sum"}).dropna()
    agg = agg.reset_index()
    agg["ts"] = (agg["timestamp"].astype("int64") // 10**6).astype(int) \
        if "timestamp" in agg.columns else (agg.iloc[:, 0].astype("int64") // 10**6).astype(int)
    agg = agg[["ts", "open", "high", "low", "close", "volume"]]
    # Drop the still-forming 4h bar, as the real fetch_4h does.
    span = 4 * 3600 * 1000
    cur_b = (_now_ms // span) * span
    agg = agg[agg["ts"] < cur_b]
    return agg.tail(bars).reset_index(drop=True)


def fake_time() -> float:
    return _now_ms / 1000.0


workdir = tempfile.mkdtemp(prefix="replay_paper_")
os.chdir(workdir)

import paper_bb  # noqa: E402

paper_bb.fetch_recent = fake_fetch_recent
paper_bb.fetch_4h = fake_fetch_4h
paper_bb.time.time = fake_time

trader = paper_bb.PaperTrader(TOKENS, resume=False)

for i in range(START, START + N_BARS):
    _now_ms = grid[i]
    bar_dt = datetime.fromtimestamp(_now_ms / 1000, tz=timezone.utc)
    trader._process_bar(bar_dt)

trader.log_f.flush()

# Strip the wall-clock stamp: it is the one genuinely non-deterministic field.
log_lines = [ln.split("] ", 1)[1] if ln.startswith("[") and "] " in ln else ln
             for ln in open("paper_bb.log").read().splitlines()]

result = {
    "bars": N_BARS,
    "balance": round(trader.balance, 6),
    "peak": round(trader.peak, 6),
    "probe_cost": round(trader.probe_cost, 6),
    "size_factor": trader.size_factor,
    "daily_freeze": trader.daily_freeze,
    "daily_sl_count": trader.daily_sl_count,
    "bar_count": trader.bar_count,
    "funnel_totals": trader.funnel_totals,
    "counters": {k: getattr(trader, k) for k in dir(trader)
                 if k.startswith("run_") and isinstance(getattr(trader, k), int)},
    "trade_log": trader.trade_log,
    "regime": trader._regime,
    "sym_states": {t: s.state for t, s in trader.sym_states.items()},
    "log": log_lines,
}
with open(OUT_PATH, "w") as f:
    json.dump(result, f, indent=1, sort_keys=True)

print(f"bars={N_BARS} balance={trader.balance:.4f} trades={len(trader.trade_log)} "
      f"log_lines={len(log_lines)} → {OUT_PATH}")
