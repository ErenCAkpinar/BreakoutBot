"""Replay every candidate coin once and save its position records to disk.

Curation asks a statistical question, and the expensive part (replaying 191k bars
per coin) has nothing to do with it. This script does the replay ONCE and writes
per-coin position records with timestamps; `wfa.py` then answers walk-forward,
PBO and Deflated-Sharpe questions over those records in seconds, so the method
can be iterated on without paying for the replay again.

    python3.12 experiments/gen_candidates.py --days 665 [--workers 6]

Reads the cached windows under backtests/data/ (nothing is fetched if they are
present). Output: experiments/candidates/<SYMBOL>_<days>d.json

Each coin is replayed on its OWN account, which is the right frame for RANKING
coins against each other — no MAX_OPEN contention, no shared drawdown guard
deciding which coin got to trade. The portfolio question is answered afterwards,
by running the SELECTED universe through backtest.run_portfolio.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import pandas as pd  # noqa: E402

import config  # noqa: E402
from backtest import CACHE_DIR, run_symbol  # noqa: E402
from config import INITIAL_BALANCE, REGIME_WARMUP_BARS, REGIME_WARMUP_DAYS  # noqa: E402

OUT = "experiments/candidates"
FINAL_EXITS = {"TP2", "TRAIL", "SL", "TIMEOUT"}


def _path(sym: str, days: int) -> str:
    """Cache path. These pickles are self-generated OHLCV frames — backtest.py /
    bench.py wrote them from the Binance fetch on this machine, and nothing
    external is ever unpickled (same convention as curate.py)."""
    return f"{CACHE_DIR}/{sym}_{days}d+{REGIME_WARMUP_DAYS}w.pkl"


def _positions_from_legs(legs: list[dict]) -> list[dict]:
    """Fold momentum legs into positions carrying OPEN and CLOSE timestamps.

    A fold boundary has to know when a position was *entered* as well as when it
    closed, or a position opened before the split and closed after it lands
    wholly in the test set and leaks the training period into it.
    """
    out: list[dict] = []
    pending: dict[str, dict] = {}
    for leg in sorted((x for x in legs if x["ts"]), key=lambda x: x["ts"]):
        sleeve, et, sym = leg["sleeve"], leg["exit_type"], leg["symbol"]
        if sleeve != "MOMENTUM":
            continue                      # probes and MR are not what is curated
        if et == "OPEN":
            pending[sym] = {"ts_open": leg["ts"], "pnl": leg["pnl"], "legs": []}
        elif sym in pending:
            p = pending[sym]
            p["pnl"] += leg["pnl"]
            p["legs"].append(et)
            if et in FINAL_EXITS:
                out.append({"ts_open": p["ts_open"], "ts_close": leg["ts"],
                            "pnl": round(p["pnl"], 6), "exit_type": et,
                            "legs": p["legs"]})
                pending.pop(sym)
    return out


def one(sym: str, days: int) -> tuple[str, int, str]:
    """Replay one coin. Returns (symbol, n_positions, message)."""
    try:
        df = pd.read_pickle(_path(sym, days))
        btc = pd.read_pickle(_path("BTCUSDT", days))
    except Exception as exc:
        return sym, 0, f"cache yok ({exc})"
    if len(df) <= REGIME_WARMUP_BARS + 65:
        return sym, 0, f"yetersiz bar ({len(df)})"

    r = run_symbol(sym, days, INITIAL_BALANCE, btc_df=btc, df=df,
                   trade_start_idx=REGIME_WARMUP_BARS)
    pos = _positions_from_legs(r["_legs"])
    # pnl here EXCLUDES the entry fee (it is on the OPEN leg, folded in above),
    # so these are all-in dollars — the same convention ledger.py decides on.
    payload = {
        "symbol": sym, "days": days,
        "risk_per_trade_usd": config.RISK_PER_TRADE_USD,
        "params": {"sl": config.SL_FULL_ATR, "tp1": config.TP1_ATR,
                   "tp2": config.TP2_ATR, "trail": config.TRAIL_ATR,
                   "timeout": config.TIMEOUT_BARS,
                   "tp1_frac": config.TP1_CLOSE_FRAC},
        "halted": r.get("halted"), "n_halts": r.get("n_halts"),
        "final_balance": r["final_balance"], "max_dd": r["max_dd"],
        "positions": pos,
    }
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/{sym}_{days}d.json", "w") as fh:
        json.dump(payload, fh)
    return sym, len(pos), "ok"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=665)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--symbols", default="")
    args = ap.parse_args()

    syms = (args.symbols.split(",") if args.symbols
            else [s for s in config._FULL_UNIVERSE if os.path.exists(_path(s, args.days))])
    missing = [s for s in config._FULL_UNIVERSE if not os.path.exists(_path(s, args.days))]
    print(f"  {len(syms)} aday · {args.days}g · {args.workers} paralel")
    if missing:
        # Never let an absent coin read as a coin that was never a candidate.
        print(f"  ⚠️  önbellekte YOK, aday dışı: {', '.join(missing)}")

    if os.getenv("BT_RESTARTS") != "1":
        # A coin that hits PEAK_DD_LIMIT stops mid-window. Rank 23 coins that way
        # and the later folds contain only the SURVIVORS — survivorship bias
        # manufactured inside the harness itself. Measured on the first 665d pass:
        # 9 of 23 halted, one after 178 of 665 days, and the walk-forward's third
        # fold was left with 7 positions total.
        print("  ⚠️  BT_RESTARTS=1 verilmedi — hard-stop'a çarpan coinler pencereyi")
        print("      yarıda bırakır ve geç fold'larda yalnızca hayatta kalanlar kalır.")
        print("      Kürasyon için: BT_RESTARTS=1 python3.12 experiments/gen_candidates.py …")

    done, halted = 0, []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(one, s, args.days): s for s in syms}
        for f in as_completed(futs):
            sym, n, msg = f.result()
            done += 1
            print(f"  [{done:>2}/{len(syms)}] {sym:<12} pozisyon={n:<4} {msg}", flush=True)

    for p in glob.glob(f"{OUT}/*_{args.days}d.json"):
        d = json.load(open(p))
        if d.get("halted"):
            halted.append(d["symbol"])
    if halted:
        print(f"\n  🛑 {len(halted)} coin pencereyi tamamlamadı: {', '.join(sorted(halted))}")
        print("      Bu kayıtlarla yapılan sıralama hayatta-kalma yanlılığı taşır.")
    print(f"\n  → {OUT}/")


if __name__ == "__main__":
    main()
