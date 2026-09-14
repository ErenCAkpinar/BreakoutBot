"""Replay recorded NEAR entries using the existing exit state machine.

Offline diagnostic only: entries are fixed, later signals are not replayed.
Run from any directory with python3.12. No live state or config is modified.
"""
import csv
import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    sys.path.insert(0, str(ROOT.parents[2]))
    strategy = importlib.import_module("strategy")
    expected = (2.25, 3.0, 6.0, 3.75, 96, 0.0, True)
    actual = (strategy.SL_FULL_ATR, strategy.TP1_ATR, strategy.TP2_ATR,
              strategy.TRAIL_ATR, strategy.TIMEOUT_BARS,
              strategy.TP1_CLOSE_FRAC, strategy.ADVERSE_FILLS)
    assert actual == expected, "Exit config changed; archive/current replay differs"
    with (ROOT / "near_5m.csv").open() as handle:
        candles = [
            {"open_ts": datetime.fromisoformat(r["timestamp"]),
             **{k: float(r[k]) for k in ("open", "high", "low", "close")}}
            for r in csv.DictReader(handle)
        ]
    trades = [
        t for t in json.loads((ROOT / "paired_trades.json").read_text())
        if "2026-09-04" <= t["start"] < "2026-09-08"
    ]
    results = []
    throttle_off = datetime(2026, 9, 6, 8, 15, tzinfo=timezone.utc)
    for trade in trades:
        start = datetime.fromisoformat(trade["start"])
        end = datetime.fromisoformat(trade["end"])
        risk = 5.0 if start < throttle_off else 10.0
        if trade["reason"] == "TP2":
            atr = (trade["exit"] - trade["entry"]) / 6.0
        elif trade["reason"] == "SL":
            atr = (trade["entry"] - trade["exit"]) / 2.25
        else:
            best = max(c["high"] for c in candles
                       if start <= c["open_ts"] < end - timedelta(minutes=5))
            atr = (best - trade["exit"]) / 3.75
        notional = risk * trade["entry"] / (2.25 * atr)
        assert round(notional * .00075, 4) == -trade["entry_fee"]
        for no_tp2 in (False, True):
            state = strategy.SymbolState(
                symbol="NEARUSDT", state=strategy.SCALE_OPEN, direction="LONG",
                test_atr=atr, full_entry=trade["entry"], full_notional=notional,
                full_sl=trade["entry"] - 2.25 * atr,
                full_tp1=trade["entry"] + 3.0 * atr,
                full_tp2=float("inf") if no_tp2 else trade["entry"] + 6.0 * atr,
                be_price=trade["entry"], trail_best=trade["entry"])
            result = None
            for candle in candles:
                if candle["open_ts"] < start:
                    continue
                events = state.process_bar(
                    {"price": {"current": candle["close"]}},
                    candle["high"], candle["low"], 50.0, 1.0)
                if events:
                    event = events[0]
                    result = {
                        "time": (candle["open_ts"] + timedelta(minutes=5)).isoformat(),
                        "price": event.exit, "reason": event.exit_type,
                        "net": event.pnl - notional * .00075}
                    break
            assert result is not None, "Input candles ended before exit"
            if not no_tp2:
                assert result["time"] == trade["end"]
                assert result["reason"] == trade["reason"]
                assert abs(result["price"] - trade["exit"]) < 1e-9
                assert abs(result["net"] - trade["net"]) < .00011
            results.append({"entry_time": trade["start"], "no_tp2": no_tp2,
                            "atr": atr, "notional": notional, "risk": risk,
                            **result})
    (ROOT / "replay_summary.json").write_text(json.dumps(results, indent=2))
    print("Verified all 6 recorded exits; wrote 12 baseline/counterfactual rows.")


if __name__ == "__main__":
    main()

