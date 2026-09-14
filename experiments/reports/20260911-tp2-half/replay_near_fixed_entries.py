"""Replay the six archived NEAR entries with the research-only half-TP2 runner.

Run with python3.12 from any directory. Uses existing candles only, checks both
archived controls, and stops at IDLE rather than the first partial exit event.
Entry signals, portfolio slot competition and later position sizes are fixed.
"""
from __future__ import annotations

import csv
import hashlib
import importlib
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent
REPO = OUTPUT.parents[2]
SOURCE = OUTPUT.parent / "20260910-near"


def replay(record, fraction, candles, runner_cls, strategy):
    source = record["source"]
    entry, atr, notional = source["entry"], record["atr"], record["notional"]
    state = runner_cls(
        symbol="NEARUSDT", state=strategy.SCALE_OPEN, direction="LONG",
        close_fraction=fraction, test_atr=atr, full_entry=entry,
        full_notional=notional, full_sl=entry - strategy.SL_FULL_ATR * atr,
        full_tp1=entry + strategy.TP1_ATR * atr,
        full_tp2=entry + strategy.TP2_ATR * atr,
        be_price=entry, trail_best=entry,
    )
    start = datetime.fromisoformat(source["start"])
    entry_fee = notional * strategy.EXEC_COST_PER_SIDE
    remaining = notional
    legs, transitions = [], []
    for candle in candles:
        if candle["open_ts"] < start:
            continue
        before = state.state
        events = state.process_bar(
            {"price": {"current": candle["close"]}},
            candle["high"], candle["low"], 50.0, 1.0,
        )
        close_time = (candle["open_ts"] + timedelta(minutes=5)).isoformat()
        if state.state != before:
            transitions.append([close_time, before, state.state])
        for event in events:
            closed = remaining * fraction if event.exit_type == "TP2_PARTIAL" else remaining
            fee = closed * strategy.EXEC_COST_PER_SIDE
            gross = closed * (event.exit - entry) / entry
            assert math.isclose(event.pnl, gross - fee, abs_tol=1e-9)
            legs.append({
                "time": close_time, "reason": event.exit_type,
                "price": event.exit, "entry_notional_closed": closed,
                "gross_pnl": gross, "exit_fee": fee, "exit_pnl": event.pnl,
            })
            remaining -= closed
        if state.state == strategy.IDLE:
            break
    assert state.state == strategy.IDLE, "Candles ended before the residual exited"
    assert math.isclose(remaining, 0.0, abs_tol=1e-9)
    assert math.isclose(sum(leg["exit_fee"] for leg in legs), entry_fee, abs_tol=1e-9)
    assert sum(leg["reason"] == "TP2_PARTIAL" for leg in legs) <= 1
    return {
        "close_fraction": fraction, "entry_fee": entry_fee,
        "exit_fee_total": sum(leg["exit_fee"] for leg in legs),
        "gross_pnl": sum(leg["gross_pnl"] for leg in legs),
        "net": sum(leg["exit_pnl"] for leg in legs) - entry_fee,
        "final_time": legs[-1]["time"], "final_reason": legs[-1]["reason"],
        "legs": legs, "transitions": transitions,
    }


def verify_archive(result, archived):
    expected = archived["result"]
    assert result["final_time"] == expected["time"]
    assert result["final_reason"] == expected["reason"]
    assert math.isclose(result["legs"][-1]["price"], expected["price"], abs_tol=1e-9)
    assert math.isclose(result["net"], expected["net"], abs_tol=1e-9)


def main():
    sys.path.insert(0, str(REPO))
    strategy = importlib.import_module("strategy")
    runner_cls = importlib.import_module("experiments.tp2_runner").TP2RunnerState
    expected = (2.25, 3.0, 6.0, 3.75, 96, 0.0, True, 0.00075)
    actual = (strategy.SL_FULL_ATR, strategy.TP1_ATR, strategy.TP2_ATR,
              strategy.TRAIL_ATR, strategy.TIMEOUT_BARS, strategy.TP1_CLOSE_FRAC,
              strategy.ADVERSE_FILLS, strategy.EXEC_COST_PER_SIDE)
    assert actual == expected, "Archived research configuration differs from current process"
    archives = json.loads((SOURCE / "replay_results.json").read_text())
    baseline = [record for record in archives if not record["no_tp2"]]
    no_target = {record["source"]["start"]: record
                 for record in archives if record["no_tp2"]}
    assert len(baseline) == len(no_target) == 6
    with (SOURCE / "near_5m.csv").open() as handle:
        candles = [{"open_ts": datetime.fromisoformat(row["timestamp"]),
                    **{key: float(row[key]) for key in ("open", "high", "low", "close")}}
                   for row in csv.DictReader(handle)]

    rows = []
    for record in baseline:
        start = record["source"]["start"]
        full = replay(record, 1.0, candles, runner_cls, strategy)
        none = replay(record, 0.0, candles, runner_cls, strategy)
        half = replay(record, 0.5, candles, runner_cls, strategy)
        verify_archive(full, record)
        verify_archive(none, no_target[start])
        assert abs(full["net"] - record["source"]["net"]) < 0.00011
        # Independent fixed-entry check: the same size-independent trail/timeout
        # governs the residual. With proportional costs, half/half must interpolate
        # the two full-notional exits; a portfolio run need not interpolate them.
        assert math.isclose(half["net"], (full["net"] + none["net"]) / 2, abs_tol=1e-9)
        rows.append({
            "entry_time": start, "entry": record["source"]["entry"],
            "initial_risk": record["risk"], "atr": record["atr"],
            "notional": record["notional"], "actual_recorded_net": record["source"]["net"],
            "baseline": full, "no_tp2": none, "half_tp2": half,
            "half_minus_baseline": half["net"] - full["net"],
        })
    sums = {variant: sum(row[variant]["net"] for row in rows)
            for variant in ("baseline", "no_tp2", "half_tp2")}
    inputs = [SOURCE / "replay_results.json", SOURCE / "near_5m.csv",
              REPO / "strategy.py", REPO / "experiments" / "tp2_runner.py",
              Path(__file__).resolve()]
    payload = {
        "scope": "Fixed-entry diagnostic, not a portfolio backtest; all times UTC",
        "notes": [
            "Same six recorded entries, exact archived ATR/notional/risk; probes excluded.",
            "One original entry fee plus proportional exit fees; no new entry fee for the runner.",
            "Same trail and original phase timeout; simulation continues until the residual exits.",
            "Later signals, cooldown, throttle and cross-symbol slot competition are not regenerated.",
        ],
        "sha256": {str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in inputs},
        "rows": rows, "total_net": sums,
        "half_minus_baseline": sums["half_tp2"] - sums["baseline"],
        "verification": {"baseline_exits_matched": 6, "no_tp2_exits_matched": 6,
                         "fee_reconciliations_passed": 18,
                         "halfway_pnl_checks_passed": 6},
    }
    (OUTPUT / "near_fixed_entry.json").write_text(json.dumps(payload, indent=2) + "\n")

    lines = [
        "# NEAR: aynı altı girişte TP2'de yarısını kapatma deneyi",
        "",
        "Bu çalışma **sabit girişlerle çıkış teşhisidir; portföy backtest'i değildir**. "
        "Saatler UTC. Gerçek girişler ile önceki incelemenin ATR, büyüklük ve riskleri "
        "korundu. Tek giriş ücreti ve her çıkışın orantılı yürütme maliyeti dahil; "
        "probe işlemleri hariçtir.",
        "",
        "TP2'de %50 kapanıyor; kalan %50 aynı 3,75 ATR trail ve mevcut fazın "
        "süre sınırıyla taşınıyor. TP2'de süre sıfırlanmıyor. Kısmi çıkıştan sonra "
        "hesap, kalan pozisyon kapanıncaya kadar devam ediyor.",
        "",
        "| Giriş (UTC) | Mevcut net | TP2 yok net | TP2'de yarısı net | Fark | Kalanın kapanışı (UTC) |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        start = datetime.fromisoformat(row["entry_time"]).strftime("%d Eyl %H:%M")
        final = datetime.fromisoformat(row["half_tp2"]["final_time"]).strftime("%d Eyl %H:%M")
        lines.append(
            f"| {start} | ${row['baseline']['net']:+.2f} | ${row['no_tp2']['net']:+.2f} "
            f"| ${row['half_tp2']['net']:+.2f} | ${row['half_minus_baseline']:+.2f} "
            f"| {final} {row['half_tp2']['final_reason']} |"
        )
    lines += [
        f"| **Toplam** | **${sums['baseline']:+.2f}** | **${sums['no_tp2']:+.2f}** "
        f"| **${sums['half_tp2']:+.2f}** | **${payload['half_minus_baseline']:+.2f}** | |",
        "",
        "Bu girişlerde yarım satış, mevcut TP2 ile hedefi tümüyle kaldırma "
        "sonucunun tam ortasında kaldı. Bu eşitlik, girişler/büyüklükler sabit ve "
        "maliyetler orantılı olduğu için oluşur. Portföyde geciken kapanışlar yeni "
        "sinyalleri, beklemeyi, risk büyüklüğünü ve diğer coinlerle yer paylaşımını "
        "değiştirebilir; portföy sonuçlarının bu ortalamaya eşit olması beklenmez.",
        "",
        "Altı mevcut çıkış ve altı TP2'siz kontrolün zaman, fiyat, çıkış türü ve "
        "net PnL'si önceki arşivle eşleşti. Her üç varyantın ücretleri uzlaştırıldı. "
        "Gerçek kaydın dört ondalık yuvarlaması ile tekrar oynatma farkı $0,00011'den küçük.",
        "",
        "Tekrar üretim: `python3.12 experiments/reports/20260911-tp2-half/replay_near_fixed_entries.py`. "
        "[Ayrıntılı JSON ve kaynak hash'leri](near_fixed_entry.json).",
    ]
    (OUTPUT / "near_fixed_entry.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"total_net": sums, "half_minus_baseline": payload["half_minus_baseline"],
                      "verification": payload["verification"]}, indent=2))


if __name__ == "__main__":
    main()
