"""Regression tests for the three measurement fixes: M1, M2, T0.

Each of these bugs was silent — the numbers looked plausible and were published.
The assertions below are the cheapest thing that would have caught them.

Synthetic records only: no network, no market data, no pickles. A hand-built
record set is a *better* test here than real data, because the nasty cases
(an MR leg tagged the live way, a probe leg with no parent, an OPEN leg that
moves the balance) are constructible on demand and impossible to mistake.
"""
from __future__ import annotations

import config
from metrics import aggregate_positions, position_stats


# ── T0: every balance-moving leg must reach the trade log ────────────────────

def _live_log():
    """A trade log shaped exactly like paper_bb writes it, post-T0.

    Momentum position = TEST open + CONFIRM_OK + FULL open + exit.
    Only the exit is a "position"; the other three are cost legs.
    """
    return [
        {"symbol": "INJUSDT", "direction": "LONG", "kind": "TEST", "sleeve": "PROBE",
         "exit_type": "OPEN",       "pnl": -0.015},   # $20 test open fee
        {"symbol": "INJUSDT", "direction": "LONG", "kind": "TEST", "sleeve": "PROBE",
         "exit_type": "CONFIRM_OK", "pnl": -0.030},   # test closed into the full
        {"symbol": "INJUSDT", "direction": "LONG", "kind": "FULL", "sleeve": "MOMENTUM",
         "exit_type": "OPEN",       "pnl": -0.675},   # $900 full open fee
        {"symbol": "INJUSDT", "direction": "LONG", "kind": "FULL", "sleeve": "MOMENTUM",
         "exit_type": "TP2",        "pnl": 21.000},
        # a probe that never became a position at all
        {"symbol": "ADAUSDT", "direction": "LONG", "kind": "TEST", "sleeve": "PROBE",
         "exit_type": "OPEN",       "pnl": -0.015},
        {"symbol": "ADAUSDT", "direction": "LONG", "kind": "TEST", "sleeve": "PROBE",
         "exit_type": "CONFIRM_FAIL", "pnl": -0.065},
        # MR leg tagged the way the LIVE bot tags it: direction, plain exit type
        {"symbol": "NEARUSDT", "direction": "MR", "sleeve": "MR",
         "exit_type": "OPEN",       "pnl": -0.150},
        {"symbol": "NEARUSDT", "direction": "MR", "sleeve": "MR",
         "exit_type": "TP",         "pnl": 5.000},
    ]


def test_trade_log_reconciles_with_balance():
    """THE assertion. sum(trade_log.pnl) must equal the balance change.

    Before T0, OPEN and probe legs debited self.balance and then hit a `continue`
    before the trade_log append (paper_bb.py:717 preceded the append at :759), so
    this identity failed by -$53.41 on the 8-coin bot and -$33.35 on the 5-coin
    one — and the published expectancy came out +$0.61/position when the
    reconciled figure was -$0.14.
    """
    log = _live_log()
    start = 1000.0
    balance = start + sum(r["pnl"] for r in log)
    assert abs(sum(r["pnl"] for r in log) - (balance - start)) < 0.01


def test_open_legs_do_not_inflate_the_position_count():
    """Logging the fees must not turn them into positions."""
    positions = aggregate_positions(_live_log())
    assert not any(p.exit_type == "OPEN" for p in positions)
    # 1 momentum (TP2) + 1 MR (TP) + 2 probe legs (CONFIRM_OK, CONFIRM_FAIL)
    sleeves = sorted(p.sleeve for p in positions)
    assert sleeves == ["MOMENTUM", "MR", "PROBE", "PROBE"]


# ── M2: sleeve detection + per-sleeve R ──────────────────────────────────────

def test_live_mr_legs_are_not_counted_as_momentum():
    """metrics.py used to read only `kind`/`sleeve`, but the live trade_log tags
    MR legs as {"direction": "MR"} and writes their exit types as plain
    TP/SL/TIMEOUT — so every live MR leg read as MOMENTUM, and MR "TP" matched
    neither PARTIAL_EXITS nor FINAL_EXITS and was dropped outright.
    """
    rec = [{"symbol": "NEARUSDT", "direction": "MR", "exit_type": "TP", "pnl": 5.0}]
    positions = aggregate_positions(rec)
    assert len(positions) == 1, "MR 'TP' leg was dropped"
    assert positions[0].sleeve == "MR"


def test_expectancy_r_uses_each_sleeve_own_risk():
    """Momentum risks $10, MR risks $5. Scoring both at $10 makes expR
    dollars-over-ten across a blend of two systems, not an R-multiple.
    """
    rec = [
        {"symbol": "A", "direction": "LONG", "exit_type": "TP2", "pnl": 10.0},  # +1.0R at $10
        {"symbol": "B", "direction": "MR",   "exit_type": "TP",  "pnl": 5.0},   # +1.0R at $5
    ]
    positions = aggregate_positions(rec)
    stats = position_stats(positions, risk_per_trade=config.RISK_PER_TRADE_USD,
                           risk_by_sleeve=config.RISK_BY_SLEEVE)
    # Both positions are +1R in their own terms, so the mean must be exactly 1.0.
    assert stats["expectancy_r"] == 1.0
    # The legacy single-denominator reading understates it (15/2/10 = 0.75).
    legacy = position_stats(positions, risk_per_trade=config.RISK_PER_TRADE_USD)
    assert legacy["expectancy_r"] == 0.75
    assert stats["by_sleeve"]["MOMENTUM"]["expectancy_r"] == 1.0
    assert stats["by_sleeve"]["MR"]["expectancy_r"] == 1.0


# ── M1: the regime warm-up must be a real requirement, not a hope ────────────

def test_regime_warmup_covers_the_ma_period():
    """regime.score_series_4h needs MA_PERIOD + SLOPE_LOOKBACK CLOSED 4h bars
    before it can label anything; until then regime.py sets score = 0.0 ->
    NEUTRAL, and LONG_SIZE_MULT["NEUTRAL"] == 0.0 means momentum trades nothing.
    """
    import regime
    required_4h = regime.MA_PERIOD + regime.SLOPE_LOOKBACK
    required_days = required_4h * 4 / 24.0
    assert config.REGIME_WARMUP_DAYS >= required_days, (
        f"REGIME_WARMUP_DAYS={config.REGIME_WARMUP_DAYS} is below the "
        f"{required_days:.1f}d the 4h regime MA needs"
    )
    assert config.REGIME_WARMUP_BARS == config.REGIME_WARMUP_DAYS * config.BARS_PER_DAY
    # The trap this guards: NEUTRAL sizes longs to zero.
    assert regime.LONG_SIZE_MULT["NEUTRAL"] == 0.0
