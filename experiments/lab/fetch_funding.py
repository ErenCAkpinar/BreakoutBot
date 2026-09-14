"""Fetch and cache perpetual funding-rate history.

    python3.12 experiments/lab/fetch_funding.py [--symbols A,B] [--since 2020-09-01]

WHY THIS IS THE NEXT STEP AND NOT ANOTHER GRID
----------------------------------------------
Four rounds of measurement have now said the same thing: the deployed system is
positive only inside the window it was selected on, coin ranking carries no
out-of-sample information, 357 broad configs beat neither buy-and-hold nor each
other, and 432 focused long/short configs fail even at ZERO transaction cost.
A fifth grid over the same OHLCV would buy the same answer at a higher price.

Funding is genuinely new information. It is not derived from price, so it can
say something price cannot, and it is the one thing the research consistently
points at: delta-neutral carry earns its return from a cash flow rather than
from being right about direction.

Two things become testable with it:

  the cash flow   A short perp RECEIVES funding while it is positive. That is a
                  return component the engine has never modelled, because until
                  now there was nothing but price.
  the positioning A high funding rate means longs are crowded and paying to stay.
                  That is a sentiment reading with no price analogue.

Binance settles funding every 8h (00:00 / 08:00 / 16:00 UTC) and history reaches
back to 2020-09, so it covers even the 2095d panel.

Cache: experiments/lab/funding/<SYMBOL>.json  (append-only, re-runs extend it)
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

OUT = "experiments/lab/funding"
PAGE = 1000                      # Binance max records per call


def _ccxt_symbol(sym: str) -> str:
    return f"{sym[:-4]}/USDT:USDT" if sym.endswith("USDT") else sym


def fetch_one(ex, sym: str, since_ms: int) -> list[dict]:
    """Paginate to the present. Each record is {ts, rate}."""
    out: list[dict] = []
    cursor = since_ms
    while True:
        try:
            batch = ex.fetch_funding_rate_history(_ccxt_symbol(sym),
                                                  since=cursor, limit=PAGE)
        except Exception as exc:
            print(f"    ⚠️  {sym}: {exc}")
            break
        if not batch:
            break
        out.extend({"ts": int(b["timestamp"]), "rate": float(b["fundingRate"])}
                   for b in batch if b.get("fundingRate") is not None)
        nxt = int(batch[-1]["timestamp"]) + 1
        if nxt <= cursor or len(batch) < PAGE:
            break
        cursor = nxt
        time.sleep(0.25)
    # De-duplicate: pagination boundaries can repeat a record, and a doubled
    # funding payment would show up as free money.
    seen, uniq = set(), []
    for r in sorted(out, key=lambda r: r["ts"]):
        if r["ts"] not in seen:
            seen.add(r["ts"])
            uniq.append(r)
    return uniq


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--since", default="2020-09-01")
    args = ap.parse_args()

    syms = (args.symbols.split(",") if args.symbols
            else panel_mod.available(665))
    since_ms = int(datetime.fromisoformat(args.since)
                   .replace(tzinfo=timezone.utc).timestamp() * 1000)
    os.makedirs(OUT, exist_ok=True)
    ex = ccxt.binanceusdm({"enableRateLimit": True})

    f = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc)  # noqa: E731
    print(f"  {len(syms)} sembol · {args.since}'den itibaren\n")
    for i, sym in enumerate(syms, 1):
        path = f"{OUT}/{sym}.json"
        recs = fetch_one(ex, sym, since_ms)
        if not recs:
            print(f"  [{i:>2}/{len(syms)}] {sym:<12} kayıt yok")
            continue
        # Append-only, as the docstring promises: merge with what is on disk,
        # de-duplicate on ts, keep the union. (Before 2026-09-14 this overwrote
        # the file and a --since re-run silently threw the history away.)
        if os.path.exists(path):
            try:
                old = json.load(open(path)).get("records", [])
            except Exception:
                old = []
            merged = {r["ts"]: r for r in old}
            merged.update({r["ts"]: r for r in recs})
            recs = [merged[k] for k in sorted(merged)]
        with open(path, "w") as fh:
            json.dump({"symbol": sym, "records": recs}, fh)
        rates = [r["rate"] for r in recs]
        ann = sum(rates) / len(rates) * 3 * 365 * 100      # 3 ödeme/gün
        print(f"  [{i:>2}/{len(syms)}] {sym:<12} {len(recs):>5} kayıt  "
              f"{f(recs[0]['ts']):%Y-%m-%d} → {f(recs[-1]['ts']):%Y-%m-%d}  "
              f"ort {ann:+6.1f}%/yıl", flush=True)
    print(f"\n  → {OUT}/")


if __name__ == "__main__":
    main()
