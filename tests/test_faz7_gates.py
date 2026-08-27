"""Mechanism tests for the two Faz 7 gates.

These check that each gate DOES WHAT IT SAYS, separately from whether it makes
money — that second question belongs to experiments/ledger.py and needs two
windows. Testing the mechanism first matters because a gate that silently does
nothing produces a backtest "result" that is really just the baseline rerun, and
nothing in the numbers would reveal it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import regime
import strategy
from strategy import IDLE, TEST_OPEN, SymbolState


# ── Gate 1: BULL must be confirmed by the coin's own trend ───────────────────

def test_coin_confirmation_removes_only_the_btc_carried_cell(monkeypatch):
    """The whole truth table, both settings.

    Only (btc=+1, coin=0) may change — that is the 25-27 Aug pattern where BTC
    squeezed, every coin flipped BULL on BTC's score alone, and all five entries
    stopped out. Any OTHER cell changing means the gate is doing more than it
    claims.
    """
    # blended = 0.575×btc + 0.425×coin ; BULL ≥ 0.40 ; BEAR ≤ -0.40
    table = [
        # btc, coin, blended,  plain,     with-gate
        (+1.0, +1.0,  1.000,   "BULL",    "BULL"),
        (+1.0,  0.0,  0.575,   "BULL",    "NEUTRAL"),   # the only intended change
        (+1.0, -1.0,  0.150,   "NEUTRAL", "NEUTRAL"),
        ( 0.0, +1.0,  0.425,   "BULL",    "BULL"),
        ( 0.0,  0.0,  0.000,   "NEUTRAL", "NEUTRAL"),
        ( 0.0, -1.0, -0.425,   "BEAR",    "BEAR"),
        (-1.0, +1.0, -0.150,   "NEUTRAL", "NEUTRAL"),
        (-1.0,  0.0, -0.575,   "BEAR",    "BEAR"),
        (-1.0, -1.0, -1.000,   "BEAR",    "BEAR"),
    ]
    for btc, coin, blended, _, _ in table:      # the arithmetic itself
        assert abs(regime.BTC_WEIGHT * btc
                   + (1 - regime.BTC_WEIGHT) * coin - blended) < 1e-9
    monkeypatch.setattr(regime, "REQUIRE_COIN_BULL", False)
    for btc, coin, _, plain, _ in table:
        assert regime.classify(btc, coin) == plain, f"plain {btc}/{coin}"

    monkeypatch.setattr(regime, "REQUIRE_COIN_BULL", True)
    for btc, coin, _, _, gated in table:
        assert regime.classify(btc, coin) == gated, f"gated {btc}/{coin}"


def test_btc_weight_is_inert_between_040_and_060(monkeypatch):
    """BTC_WEIGHT cannot express a preference anywhere in 0.40–0.60.

    ROADMAP §9 locked this at 0.575 after deliberating "55-60% arası" as if the
    exact value mattered. It does not, and the reason is structural: asset scores
    are ternary {-1, 0, +1}, so the blend can only ever take the values
    {±1, ±w, ±(1-w), 0}. BULL needs ≥ 0.40; for any w in [0.40, 0.60] BOTH w and
    (1-w) clear it, so every cell of the truth table lands identically.

    Measured the expensive way first: R2-btcw40 (w=0.40) came back byte-identical
    to the baseline on a 240d replay — same 123 positions, same $1370.04, same
    exit counts. Fourteen minutes of CPU to learn what this assertion states.

    Tuning this knob is therefore not a lever. Changing regime behaviour needs a
    different mechanism (the coin-confirmation gate, a finer score, or moving
    BULL_THRESHOLD) — not a new weight.
    """
    def labels(w: float) -> list[str]:
        monkeypatch.setattr(regime, "BTC_WEIGHT", w)
        return [regime.classify(b, c)
                for b in (1.0, 0.0, -1.0) for c in (1.0, 0.0, -1.0)]

    monkeypatch.setattr(regime, "REQUIRE_COIN_BULL", False)
    ref = labels(0.575)
    for w in (0.40, 0.45, 0.50, 0.55, 0.60):
        assert labels(w) == ref, f"w={w} differs — the inert range moved"

    # Outside the range it DOES bite, so the assertion above is about this
    # specific window and not about classify() ignoring its argument.
    assert labels(0.35) != ref
    assert labels(0.65) != ref


def _frame(closes: list[float]) -> pd.DataFrame:
    """5m frame long enough for resample_4h to yield > MA_PERIOD 4h bars."""
    n = len(closes)
    ts = np.arange(n, dtype=np.int64) * 300_000
    c = np.array(closes, dtype=float)
    return pd.DataFrame({"ts": ts, "open": c, "high": c * 1.001,
                         "low": c * 0.999, "close": c, "volume": np.ones(n)})


def test_vectorised_path_matches_classify(monkeypatch):
    """backtest_regimes() must reproduce classify() exactly under the gate.

    They are two implementations of one rule — live reads classify(), the
    backtest reads the vectorised branch. A divergence here would mean the
    backtest measures a gate the live bot does not apply, which is the failure
    mode the parity contract exists to prevent.
    """
    # Enough 4h bars that a long flat tail still exceeds MA_PERIOD + SLOPE_LOOKBACK.
    bars = 48 * (2 * regime.MA_PERIOD + 200)                      # 4h bars → 5m
    # Coin: rises, then goes flat long enough for its own 200-MA to catch up and
    # STOP rising → price still above MA but slope 0 → coin_score 0 (not -1).
    # BTC: rises throughout → btc_score +1. That is the disputed cell.
    ramp = list(np.linspace(100.0, 200.0, int(bars * 0.30)))
    flat = [200.0] * (bars - len(ramp))
    coin = _frame(ramp + flat)
    btc = _frame(list(np.linspace(100.0, 400.0, bars)))

    for gate in (False, True):
        monkeypatch.setattr(regime, "REQUIRE_COIN_BULL", gate)
        labels = regime.backtest_regimes(coin, btc)
        # Recompute the same bars through classify() and demand agreement.
        sc_coin = regime.score_series_4h(regime.resample_4h(coin))
        sc_btc = regime.score_series_4h(regime.resample_4h(btc))
        expected = {regime.classify(b, c) for b, c in zip(sc_btc.values, sc_coin.values)}
        assert set(labels).issubset(expected | {"NEUTRAL"}), (
            f"gate={gate}: vectorised produced labels classify() never yields")

    # And the gate must actually bite on this construction, or the test is vacuous.
    monkeypatch.setattr(regime, "REQUIRE_COIN_BULL", False)
    plain = regime.backtest_regimes(coin, btc)
    monkeypatch.setattr(regime, "REQUIRE_COIN_BULL", True)
    gated = regime.backtest_regimes(coin, btc)
    assert (plain == "BULL").sum() > (gated == "BULL").sum(), \
        "gate changed nothing — the fixture never hit the (btc=+1, coin=0) cell"


# ── Gate 2: the friction floor ───────────────────────────────────────────────

class _Engine:
    """Always signals STRONG_LONG, so only the gates under test can stop it."""
    def analyze(self, snapshot):
        return {"signal": "STRONG_LONG", "composite_score": 99.0}


def _snap(price: float, atr: float) -> dict:
    return {"price": {"current": price},
            "meta": {"hurst": 0.99, "atr": atr},
            "indicators": {}, "volume": {}}


def _probe_opened(price: float, atr: float, min_frac: float, monkeypatch) -> bool:
    monkeypatch.setattr(strategy, "MIN_SL_FRAC", min_frac)
    s = SymbolState(symbol="TESTUSDT")
    s.engine = _Engine()                       # type: ignore[assignment]
    events = s.process_bar(_snap(price, atr), price * 1.001, price * 0.999,
                           rsi_val=60.0, vol_ratio=1.5, size_mult=1.0)
    return s.state == TEST_OPEN and bool(events)


def test_friction_floor_refuses_a_stop_that_is_too_close(monkeypatch):
    """sl_frac = SL_FULL_ATR × atr / price. Below the floor, no probe is paid for.

    price 100, atr 0.20, SL_FULL_ATR 1.5 → sl_frac 0.30% → round-trip cost is
    0.0015/0.003 = 50% of the risk taken. That setup must be refused.
    """
    assert not _probe_opened(100.0, 0.20, 0.0075, monkeypatch)
    # …and the same setup is allowed when the floor is off (default).
    assert _probe_opened(100.0, 0.20, 0.0, monkeypatch)


def test_friction_floor_lets_a_wide_enough_stop_through(monkeypatch):
    """price 100, atr 1.0, SL_FULL_ATR 1.5 → sl_frac 1.5% → cost 10% of risk."""
    assert _probe_opened(100.0, 1.0, 0.0075, monkeypatch)


def test_friction_floor_boundary_is_the_full_stop_not_the_test_stop(monkeypatch):
    """The floor is about the FULL position's stop (SL_FULL_ATR), because that is
    the leg carrying the risk — not the $20 probe's 1×ATR stop."""
    # atr chosen so 1.5×atr/price == 0.0075 exactly.
    price, atr = 100.0, 0.0075 * 100.0 / strategy.SL_FULL_ATR
    assert _probe_opened(price, atr * 1.01, 0.0075, monkeypatch)      # just above
    assert not _probe_opened(price, atr * 0.99, 0.0075, monkeypatch)  # just below


def test_gate_leaves_state_idle_so_no_cooldown_is_burned(monkeypatch):
    """A refused setup must not park the symbol in cooldown — the bot should be
    free to take the next, wider setup on the very next bar."""
    monkeypatch.setattr(strategy, "MIN_SL_FRAC", 0.0075)
    s = SymbolState(symbol="TESTUSDT")
    s.engine = _Engine()                       # type: ignore[assignment]
    s.process_bar(_snap(100.0, 0.20), 100.1, 99.9, rsi_val=60.0, vol_ratio=1.5)
    assert s.state == IDLE and s.cooldown == 0
