"""Research-only TP2 runner: fill chronology, risk occupancy and cash accounting."""
from __future__ import annotations

import math

import pytest

import backtest
import metrics
import strategy
from experiments import tp2_runner
from strategy import IDLE, SCALE_OPEN, TEST_OPEN, TRAILING, SymbolState, Trade


def _set_constant(monkeypatch, name, value):
    """Cover both a qualified strategy reference and an imported constant."""
    monkeypatch.setattr(strategy, name, value)
    if hasattr(tp2_runner, name):
        monkeypatch.setattr(tp2_runner, name, value)


@pytest.fixture(autouse=True)
def fixed_geometry(monkeypatch):
    # Hand calculations below use a $1,000 entry notional at price 100,
    # entry ATR 1 and the adopted research fill convention.
    for name, value in {
        "ADVERSE_FILLS": True,
        "TP1_CLOSE_FRAC": 0.0,
        "TRAIL_ATR": 3.75,
        "TIMEOUT_BARS": 96,
        "EXEC_COST_PER_SIDE": 0.00075,
    }.items():
        _set_constant(monkeypatch, name, value)


def _state(direction="LONG", fraction=0.5, cls=None, **overrides):
    mult = 1 if direction == "LONG" else -1
    fields = {
        "symbol": "NEARUSDT", "direction": direction, "state": TRAILING,
        "test_atr": 1.0, "full_entry": 100.0, "full_notional": 1000.0,
        "full_tp1": 100.0 + mult * 3.0,
        "full_tp2": 100.0 + mult * 6.0,
        "full_sl": 100.0, "trail_best": 100.0 + mult * 4.0,
        "tp1_hit": True, "bars_held": 10,
    }
    fields.update(overrides)
    if cls is SymbolState:
        return cls(**fields)
    return tp2_runner.TP2RunnerState(close_fraction=fraction, **fields)


def _bar(state, best=7.0, worst=2.0, close=5.0):
    """Offsets in the favorable direction; mirror the path for SHORT."""
    mult = 1 if state.direction == "LONG" else -1
    endpoints = [100.0 + mult * best, 100.0 + mult * worst]
    return state.process_bar(
        {"price": {"current": 100.0 + mult * close}},
        high=max(endpoints), low=min(endpoints), rsi_val=60.0, vol_ratio=2.0,
    )


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_half_closes_once_and_surviving_bar_only_ratchets_after_fills(direction):
    state = _state(direction)
    first = _bar(state)
    assert len(first) == 1
    assert first[0].exit_type == "TP2_PARTIAL"
    assert first[0].exit == (106.0 if direction == "LONG" else 94.0)
    assert first[0].pnl == pytest.approx(29.625)  # $500 * 6% - $0.375
    assert state.full_notional == 500.0
    assert state.state == TRAILING
    assert state.bars_held == 11
    assert state.trail_best == (107.0 if direction == "LONG" else 93.0)
    assert state.full_tp2 == (math.inf if direction == "LONG" else -math.inf)

    # The partial bar's low/high crosses the NEW trail. It must not cause a
    # hindsight exit; this second bar again crosses TP2 but must not sell again.
    assert _bar(state, best=8.0, worst=4.0, close=6.0) == []
    assert state.full_notional == 500.0
    assert state.bars_held == 12
    assert sum(s.state in (SCALE_OPEN, TRAILING) for s in [state]) == 1

    final = _bar(state, best=6.0, worst=3.0, close=4.0)
    assert [event.exit_type for event in final] == ["TRAIL"]
    assert final[0].pnl == pytest.approx(20.875)  # $500 * 4.25% - $0.375
    assert state.state == IDLE


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_existing_trail_beats_tp2_on_an_ambiguous_bar(direction):
    state = _state(direction)
    events = _bar(state, best=7.0, worst=0.0, close=5.0)
    assert [event.exit_type for event in events] == ["TRAIL"]
    assert events[0].exit == (100.25 if direction == "LONG" else 99.75)
    assert events[0].pnl == pytest.approx(1.75)  # $1,000 * 0.25% - $0.75
    assert state.state == IDLE


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_gap_through_existing_trail_keeps_adverse_range_clamp(direction):
    state = _state(direction)
    events = _bar(state, best=-1.0, worst=-3.0, close=-2.0)
    assert [event.exit_type for event in events] == ["TRAIL"]
    assert events[0].exit == (99.0 if direction == "LONG" else 101.0)
    assert events[0].pnl == pytest.approx(-10.75)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_tp2_and_timeout_same_bar_bank_half_then_close_remainder(direction):
    state = _state(direction, bars_held=95)
    events = _bar(state, best=7.0, worst=2.0, close=4.0)
    assert [event.exit_type for event in events] == ["TP2_PARTIAL", "TIMEOUT"]
    assert events[0].pnl == pytest.approx(29.625)
    assert events[1].exit == (104.0 if direction == "LONG" else 96.0)
    assert events[1].pnl == pytest.approx(19.625)
    assert sum(event.pnl for event in events) == pytest.approx(49.25)
    assert state.state == IDLE


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_partial_does_not_restart_timeout_clock(direction):
    state = _state(direction, bars_held=94)
    assert [event.exit_type for event in _bar(state)] == ["TP2_PARTIAL"]
    assert state.bars_held == 95
    assert state.state == TRAILING
    events = _bar(state, best=8.0, worst=4.0, close=5.0)
    assert [event.exit_type for event in events] == ["TIMEOUT"]
    assert events[0].pnl == pytest.approx(24.625)
    assert state.state == IDLE


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_full_fraction_preserves_base_event_and_state_trace(direction):
    runner, base = _state(direction, fraction=1.0), _state(direction, cls=SymbolState)
    for best, worst, close in [(5.0, 2.0, 4.0), (7.0, 2.0, 6.0)]:
        assert _bar(runner, best, worst, close) == _bar(base, best, worst, close)
        for name in SymbolState.__dataclass_fields__:
            if name != "engine":
                assert getattr(runner, name) == getattr(base, name), name


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_zero_fraction_is_existing_trail_without_a_tp2_sale(direction):
    unreachable = math.inf if direction == "LONG" else -math.inf
    runner = _state(direction, fraction=0.0)
    base = _state(direction, cls=SymbolState, full_tp2=unreachable)
    assert _bar(runner) == _bar(base) == []
    assert runner.full_notional == 1000.0
    assert runner.bars_held == base.bars_held == 11
    assert _bar(runner, 6.0, 2.0, 3.0) == _bar(base, 6.0, 2.0, 3.0)
    assert runner.state == base.state == IDLE


def test_new_full_position_rearms_tp2_after_previous_runner_closed(monkeypatch):
    state = _state()
    assert _bar(state)[0].exit_type == "TP2_PARTIAL"
    assert _bar(state, best=6.0, worst=2.0, close=3.0)[0].exit_type == "TRAIL"

    # Supply a new confirmed probe, using the base class's actual scale-up
    # transition to ensure an old infinite TP2 does not leak into the next trade.
    state.state = TEST_OPEN
    state.direction = "LONG"
    state.test_entry = 99.0
    state.test_sl = 90.0
    state.test_atr = 1.0
    monkeypatch.setattr(state, "_confirm", lambda *args: True)
    opened = _bar(state, best=1.0, worst=0.0, close=0.0)
    assert any(event.kind == "FULL" and event.exit_type == "OPEN" for event in opened)
    assert state.state == SCALE_OPEN
    assert state.full_tp2 == 106.0
    assert _bar(state, best=4.0, worst=1.0, close=3.0) == []
    assert state.state == TRAILING
    assert _bar(state)[0].exit_type == "TP2_PARTIAL"


def test_installed_runner_accounts_two_exits_as_one_position(monkeypatch):
    # Registration is explicitly scoped to this test's process-global patches.
    monkeypatch.setattr(strategy, "SymbolState", SymbolState)
    monkeypatch.setattr(metrics, "PARTIAL_EXITS", set(metrics.PARTIAL_EXITS))
    tp2_runner.install_runner(0.5)
    assert isinstance(strategy.SymbolState(symbol="NEARUSDT"), tp2_runner.TP2RunnerState)

    state = _state()
    partial = _bar(state)
    opened = Trade("NEARUSDT", "LONG", "FULL", 100.0, 0.0, -0.75, "OPEN")
    legs: list[dict] = []
    tally = backtest.new_tally()
    delta = backtest.account_events([opened, *partial], [], legs, tally)
    assert metrics.aggregate_positions(legs) == []  # runner still genuinely open
    final = _bar(state, best=6.0, worst=2.0, close=3.0)
    delta += backtest.account_events(final, [], legs, tally)

    # $30 + $16.25 gross; one entry fee $0.75 and two $0.375 exits.
    assert delta == pytest.approx(44.75)
    assert sum(leg["pnl"] for leg in legs) == pytest.approx(delta)
    positions = metrics.aggregate_positions(legs)
    assert len(positions) == 1
    assert positions[0].legs == ["TP2_PARTIAL", "TRAIL"]
    assert positions[0].partial
    assert positions[0].pnl == pytest.approx(45.50)  # metrics exclude OPEN fee
    assert tally["full"] == 1
    assert tally["TP2_PARTIAL"] == 1
    assert tally["TRAIL"] == 1


@pytest.mark.parametrize("fraction", [-0.1, 1.1, math.nan, math.inf, -math.inf])
def test_invalid_fraction_is_rejected(fraction):
    with pytest.raises(ValueError):
        _state(fraction=fraction)


@pytest.mark.parametrize("name,value", [("ADVERSE_FILLS", False), ("TP1_CLOSE_FRAC", 0.5)])
def test_unsupported_fill_or_multiple_partial_configuration_is_rejected(monkeypatch, name, value):
    _set_constant(monkeypatch, name, value)
    with pytest.raises(ValueError):
        _state()
