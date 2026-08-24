"""
Coin curation harness — rank a symbol set by POOLED position metrics.

Why this exists: the active 8-coin universe was curated (2026-07-29) under the OLD
exit structure (trail 1.5xATR + 50% partial at TP1). The bot now runs E6
(X_TRAIL_ATR=2.5, X_TP1_CLOSE_FRAC=0.0). A trend-following exit rewards different
coins than a scale-out exit, so the curation has to be re-run under the params that
are actually deployed — otherwise the universe is fitted to a system that no longer
exists.

Usage (params come from env, exactly like bench.py arms):
    X_TP1_CLOSE_FRAC=0.0 X_TRAIL_ATR=2.5 python3.12 curate.py --days 90 --tag E6

    --pinned   reuse backtests/data (the 2026-06-02 pinned window, no network)
    --days N   window length; fresh fetch cached under backtests/data_curation/
    --symbols  comma-separated; default = config._FULL_UNIVERSE (23)

Never writes to backtests/data/ — the pinned phase-comparison dataset stays intact.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import pandas as pd

# Run from anywhere: anchor to the repo root so the cache paths below resolve.
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import config
from backtest import fetch_history, run_symbol
from config import (INITIAL_BALANCE, RISK_PER_TRADE_USD, RISK_BY_SLEEVE,
                    REGIME_WARMUP_DAYS, REGIME_WARMUP_BARS)
from metrics import position_stats

PINNED = "backtests/data"
FRESH = "backtests/data_curation"


def load(symbols: list[str], days: int, pinned: bool, asof: str) -> dict[str, pd.DataFrame]:
    """Load OHLCV. pinned=True reads the frozen dataset; else fetch+cache separately.

    M1: in FRESH mode we fetch `days + REGIME_WARMUP_DAYS` so the 4h regime MA is
    already warm on the first traded bar. The cache key carries the warm-up so the
    old (cold-regime) pickles are never silently reused — they measured a different
    thing. In PINNED mode we cannot refetch, so main() carves the warm-up out of the
    front of the frozen frame instead and trades a correspondingly shorter window.

    Pickles here are self-generated OHLCV frames (this script / bench.py wrote them
    from the Binance fetch); nothing external is ever unpickled.
    """
    cache = PINNED if pinned else FRESH
    os.makedirs(cache, exist_ok=True)
    fetch_days = days if pinned else days + REGIME_WARMUP_DAYS
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols + ["BTCUSDT"]:
        if sym in out:
            continue
        path = (f"{cache}/{sym}_{days}d.pkl" if pinned
                else f"{cache}/{sym}_{days}d+{REGIME_WARMUP_DAYS}w_{asof}.pkl")
        if os.path.exists(path):
            out[sym] = pd.read_pickle(path)
        else:
            if pinned:
                print(f"  [MISSING pinned] {sym} {days}d — skipping", flush=True)
                continue
            print(f"  [fetch] {sym} {days}d +{REGIME_WARMUP_DAYS}d warm-up …", flush=True)
            for attempt in range(3):
                try:
                    df = fetch_history(sym, fetch_days, silent=True)
                    break
                except Exception as e:  # transient rate-limit / network
                    print(f"    retry {attempt+1}: {e}", flush=True)
                    time.sleep(5)
            else:
                print(f"  [FAIL] {sym} — skipped", flush=True)
                continue
            df.to_pickle(path)
            out[sym] = df
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--pinned", action="store_true")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--tag", default="run")
    ap.add_argument("--asof", default="2026-08-22")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    symbols = (args.symbols.split(",") if args.symbols
               else list(config._FULL_UNIVERSE))

    print(f"\n{'='*78}")
    print(f"  CURATION: {args.tag}   window={args.days}d  "
          f"{'PINNED 2026-06-02' if args.pinned else 'FRESH asof ' + args.asof}")
    print(f"  params: TRAIL_ATR={config.TRAIL_ATR}  TP1_CLOSE_FRAC={config.TP1_CLOSE_FRAC}  "
          f"TP1={config.TP1_ATR} TP2={config.TP2_ATR} SL={config.SL_FULL_ATR}")
    print(f"{'='*78}\n", flush=True)

    # M1: regimes need REGIME_WARMUP_DAYS of history before the first traded bar.
    # FRESH fetches it extra; PINNED carves it out of the frozen frame, which
    # shortens the tradable window by the same amount.
    if args.pinned:
        trade_days = args.days - REGIME_WARMUP_DAYS
        if trade_days <= 0:
            print(f"  pinned {args.days}d is shorter than the {REGIME_WARMUP_DAYS}d "
                  f"regime warm-up — nothing left to trade. Abort.")
            return
        print(f"  ⚠️  PINNED: first {REGIME_WARMUP_DAYS}d used as regime warm-up → "
              f"effective trading window {trade_days}d (was {args.days}d).")
    else:
        trade_days = args.days
    warm_bars = REGIME_WARMUP_BARS
    skipped: list[tuple[str, str]] = []

    data = load(symbols, args.days, args.pinned, args.asof)
    if "BTCUSDT" not in data:
        print("BTC data missing — cannot compute regime. Abort.")
        return
    btc = data["BTCUSDT"]

    rows = []
    for sym in symbols:
        if sym not in data:
            continue
        t0 = time.time()
        df_sym = data[sym].copy()
        if len(df_sym) <= warm_bars + 65:
            print(f"  [SKIP] {sym} — only {len(df_sym)} bars, need "
                  f">{warm_bars + 65} for regime warm-up + indicators", flush=True)
            skipped.append((sym, "insufficient bars for regime warm-up"))
            continue
        r = run_symbol(sym, trade_days, INITIAL_BALANCE,
                       btc_df=btc.copy(), df=df_sym,
                       trade_start_idx=warm_bars)
        pos = r.get("_positions", [])
        st = position_stats(pos, risk_per_trade=RISK_PER_TRADE_USD,
                            risk_by_sleeve=RISK_BY_SLEEVE)
        by = st.get("by_sleeve") or {}
        mom = by.get("MOMENTUM") or {}
        mr  = by.get("MR") or {}
        rows.append({
            "symbol": sym,
            # M2: rank on the MOMENTUM sleeve — that is the universe being curated.
            # The pooled figure blends momentum($10 risk) with MR($5) and is not an
            # R-multiple; both are reported so the MR question stays visible.
            "n":       mom.get("n", 0),
            "wr":      mom.get("win_rate", 0.0),
            "payoff":  st["payoff"],
            "exp_r":   mom.get("expectancy_r"),
            "mr_n":    mr.get("n", 0),
            "mr_exp_r": mr.get("expectancy_r"),
            "pooled_r": st["expectancy_r"],
            "pf": st["profit_factor"],
            "ret": round(r["total_return"], 2),
            "dd": round(r["max_dd"], 2),
        })
        print(f"  {sym:11s} MOM n={mom.get('n',0):3d} expR {str(mom.get('expectancy_r')):>7}  |  "
              f"MR n={mr.get('n',0):3d} expR {str(mr.get('expectancy_r')):>7}  |  "
              f"ret {r['total_return']:+7.2f}%  DD {r['max_dd']:+6.2f}%   "
              f"({time.time()-t0:.0f}s)", flush=True)

    # ── Ranking table ────────────────────────────────────────────────────────
    ranked = sorted(rows, key=lambda x: (x["exp_r"] is None, -(x["exp_r"] or -99)))
    print(f"\n{'─'*78}")
    print(f"  RANKING by expectancy/R   ({args.tag}, {args.days}d)")
    print(f"{'─'*78}")
    print(f"  {'#':>2} {'symbol':11s} {'momN':>5} {'WR%':>6} {'momExpR':>8} "
          f"{'mrN':>4} {'mrExpR':>8} {'pooled':>8} {'ret%':>8} {'DD%':>7}")
    for i, x in enumerate(ranked, 1):
        print(f"  {i:>2} {x['symbol']:11s} {x['n']:>5} {x['wr']:>6.1f} "
              f"{str(x['exp_r']):>8} {x['mr_n']:>4} {str(x['mr_exp_r']):>8} "
              f"{str(x['pooled_r']):>8} {x['ret']:>+8.2f} {x['dd']:>+7.2f}")

    # ── Set-level summary ────────────────────────────────────────────────────
    print(f"{'─'*78}")
    n_pos = sum(x["n"] for x in rows)
    n_ok = sum(1 for x in rows if (x["exp_r"] or 0) > 0)
    print(f"  positive expectancy: {n_ok}/{len(rows)} symbols   total MOM positions: {n_pos}")
    missing = [s2 for s2 in symbols if s2 not in {x["symbol"] for x in rows}]
    if missing or skipped:
        # Never let a silently-absent symbol look like a symbol that was never a
        # candidate — a 19-of-23 run must not read as a 19-symbol run.
        for sym2, why in skipped:
            print(f"  EXCLUDED {sym2:11s} — {why}")
        for sym2 in missing:
            if sym2 not in {a for a, _ in skipped}:
                print(f"  EXCLUDED {sym2:11s} — no data loaded (fetch failed or pinned miss)")
        print(f"  scored {len(rows)} of {len(symbols)} candidates")
    print(f"{'='*78}\n")

    if args.out:
        with open(args.out, "w") as f:
            json.dump({"tag": args.tag, "days": args.days,
                       "pinned": args.pinned, "asof": args.asof,
                       "trail": config.TRAIL_ATR, "tp1_frac": config.TP1_CLOSE_FRAC,
                       "rows": rows}, f, indent=2)
        print(f"  → {args.out}")


if __name__ == "__main__":
    main()
