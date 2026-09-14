"""Fetch and cache USDⓈ-M perpetual OHLCV at a coarse timeframe (default 4h).

    python3.12 experiments/lab/fetch_perp.py [--tf 4h] [--since 2020-09-01] [--symbols A,B]

basis.py used to take perp closes from the 5m backtest cache, which ends when
that cache was last pinned. The basis trade's out-of-sample window (DEFTER Tur
13) needs perp closes past that date on the same grid as the spot cache, so the
perp leg gets its own append-only cache here, fetched the same way spot is.

Cache: experiments/lab/perp/<SYMBOL>_<tf>.json  (append-only, de-duplicated on ts)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import ccxt

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
os.chdir(_ROOT)

import panel as panel_mod  # noqa: E402

OUT = "experiments/lab/perp"
PAGE = 1000


def fetch_one(ex, sym: str, tf: str, since_ms: int) -> list[list]:
    out: list[list] = []
    cursor = since_ms
    step = panel_mod.TF_MINUTES[tf] * 60_000
    while True:
        batch = ex.fetch_ohlcv(sym, tf, since=cursor, limit=PAGE)
        if not batch:
            break
        out.extend([[b[0], b[4]] for b in batch])       # ts, close
        if len(batch) < PAGE:
            break
        cursor = batch[-1][0] + step
        time.sleep(0.2)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--since", default="2020-09-01")
    ap.add_argument("--symbols", default="")
    args = ap.parse_args()
    syms = args.symbols.split(",") if args.symbols else panel_mod.available(665)
    since_ms = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp() * 1000)
    os.makedirs(OUT, exist_ok=True)
    ex = ccxt.binanceusdm({"enableRateLimit": True})
    print(f"  {len(syms)} sembol · perp {args.tf} · {args.since}'den\n")
    for i, sym in enumerate(syms, 1):
        bars = fetch_one(ex, sym, args.tf, since_ms)
        path = f"{OUT}/{sym}_{args.tf}.json"
        if os.path.exists(path):
            try:
                old = json.load(open(path)).get("bars", [])
            except Exception:
                old = []
            merged = {b[0]: b for b in old}
            merged.update({b[0]: b for b in bars})
            bars = [merged[k] for k in sorted(merged)]
        with open(path, "w") as fh:
            json.dump({"symbol": sym, "tf": args.tf, "bars": bars}, fh)
        a = datetime.fromtimestamp(bars[0][0] / 1000, tz=timezone.utc) if bars else None
        b = datetime.fromtimestamp(bars[-1][0] / 1000, tz=timezone.utc) if bars else None
        print(f"  [{i:>2}/{len(syms)}] {sym:<12} {len(bars):>6} bar  {a:%Y-%m-%d} → {b:%Y-%m-%d}" if bars
              else f"  [{i:>2}/{len(syms)}] {sym:<12} bar yok", flush=True)
    print(f"\n  → {OUT}/")


if __name__ == "__main__":
    main()
