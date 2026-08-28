"""Fetch and cache SPOT klines — the missing leg of a real basis trade.

    python3.12 experiments/lab/fetch_spot.py [--tf 4h] [--since 2020-09-01]

WHY SPOT
--------
The funding study found a real mechanism (funding income positive in 6 of 6
sub-periods over 5.8 years) buried under price noise it did not need to carry.
That noise was an artefact of the construction: shorting a high-funding perp
against a long in a DIFFERENT low-funding perp is beta-neutral but exposed to
the relative move between two unrelated coins.

A real carry trade is long spot and short perp on the SAME asset. The two legs
track each other, so the price risk is structurally near-zero and what remains
is the funding income plus the small drift of the basis itself. That trade needs
spot prices, which this repo has never had — everything in it is perp-only.

4h bars: a carry position is held for days or weeks, so finer resolution would
add download time and nothing else.

Cache: experiments/lab/spot/<SYMBOL>_<tf>.json
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
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import panel as panel_mod  # noqa: E402

OUT = "experiments/lab/spot"
PAGE = 1000


def fetch_one(ex, sym: str, tf: str, since_ms: int) -> list[list]:
    unified = f"{sym[:-4]}/USDT"
    out: list[list] = []
    cursor = since_ms
    while True:
        try:
            batch = ex.fetch_ohlcv(unified, tf, since=cursor, limit=PAGE)
        except Exception as exc:
            print(f"    ⚠️  {sym}: {exc}")
            break
        if not batch:
            break
        out.extend(batch)
        nxt = int(batch[-1][0]) + 1
        if nxt <= cursor or len(batch) < PAGE:
            break
        cursor = nxt
        time.sleep(0.2)
    seen, uniq = set(), []
    for b in sorted(out, key=lambda b: b[0]):
        if b[0] not in seen:
            seen.add(b[0])
            uniq.append([int(b[0]), float(b[4])])      # ts, close only
    return uniq


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--since", default="2020-09-01")
    ap.add_argument("--symbols", default="")
    args = ap.parse_args()

    syms = (args.symbols.split(",") if args.symbols
            else sorted({os.path.basename(p)[:-5]
                         for p in __import__("glob").glob("experiments/lab/funding/*.json")}))
    if not syms:
        syms = panel_mod.available(665)
    since_ms = int(datetime.fromisoformat(args.since)
                   .replace(tzinfo=timezone.utc).timestamp() * 1000)
    os.makedirs(OUT, exist_ok=True)
    ex = ccxt.binance({"enableRateLimit": True})
    f = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc)  # noqa: E731

    print(f"  {len(syms)} sembol · spot {args.tf} · {args.since}'den\n")
    for i, sym in enumerate(syms, 1):
        bars = fetch_one(ex, sym, args.tf, since_ms)
        if not bars:
            print(f"  [{i:>2}/{len(syms)}] {sym:<12} kayıt yok")
            continue
        with open(f"{OUT}/{sym}_{args.tf}.json", "w") as fh:
            json.dump({"symbol": sym, "tf": args.tf, "bars": bars}, fh)
        print(f"  [{i:>2}/{len(syms)}] {sym:<12} {len(bars):>6} bar  "
              f"{f(bars[0][0]):%Y-%m-%d} → {f(bars[-1][0]):%Y-%m-%d}", flush=True)
    print(f"\n  → {OUT}/")


if __name__ == "__main__":
    main()
