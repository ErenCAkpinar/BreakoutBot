"""E16 — a coin dropped from TOKENS while holding a position must not vanish.

Before this, `_load_state` skipped any saved symbol absent from the current
universe. The position ceased to exist: no closing leg in `trade_log`, its
unrealized PnL never booked, and on a `--testnet` bot the exchange position left
open with nothing managing it. It already happened during the 8→5 shrink on
2026-08-22, and every re-curation is another chance for it — which is exactly why
it gets a test before the next one.
"""
from __future__ import annotations

import json

import pytest

import paper_bb
from paper_bb import PaperTrader
from strategy import IDLE, SCALE_OPEN, TRAILING


def _state(sym_states: dict) -> dict:
    return {
        "balance": 950.0, "peak": 1000.0, "daily_start": 950.0,
        "daily_day": "2026-08-27", "daily_freeze": False, "daily_sl_count": 0,
        "bar_count": 8000, "trade_log": [], "sym_states": sym_states,
        "mr_states": {}, "regime": {}, "regime_4h_ts": 0,
    }


def _sym(state: str, entry: float = 4.5) -> dict:
    return {
        "state": state, "direction": "LONG", "cooldown": 0,
        "test_entry": entry, "test_sl": entry * 0.99, "test_atr": entry * 0.01,
        "full_entry": entry, "full_sl": entry * 0.98, "full_tp1": entry * 1.03,
        "full_tp2": entry * 1.06, "full_notional": 400.0, "tp1_hit": False,
        "be_price": entry, "trail_best": entry, "bars_held": 5,
    }


@pytest.fixture
def trader(tmp_path, monkeypatch):
    """A trader on the curated 2-coin universe, resuming from a state file that
    still holds a THIRD coin — the one the curation just dropped."""
    monkeypatch.setattr(paper_bb, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(paper_bb, "LOG_FILE", str(tmp_path / "bot.log"))

    def _build(saved: dict) -> PaperTrader:
        with open(paper_bb.STATE_FILE, "w") as f:
            json.dump(_state(saved), f)
        return PaperTrader(tokens=["UNIUSDT", "INJUSDT"], resume=True)

    return _build


def test_orphan_with_an_open_position_is_adopted_not_dropped(trader):
    t = trader({"UNIUSDT": _sym(IDLE), "LDOUSDT": _sym(SCALE_OPEN)})
    assert "LDOUSDT" in t.winddown, "dropped coin's open position vanished"
    assert t.sym_states["LDOUSDT"].state == SCALE_OPEN
    assert t.sym_states["LDOUSDT"].full_entry == 4.5, "position not restored"


def test_orphan_that_is_already_flat_is_not_adopted(trader):
    """Only OPEN positions justify keeping a retired symbol on the book. A flat
    one is just a stale key — adopting it would resurrect coins forever."""
    t = trader({"UNIUSDT": _sym(IDLE), "LDOUSDT": _sym(IDLE)})
    assert t.winddown == set()
    assert "LDOUSDT" not in t.sym_states


def test_winddown_symbol_is_barred_from_new_entries(trader):
    """It must manage the exit and nothing else. If it could re-enter, dropping a
    coin from TOKENS would do nothing at all while it happened to be in a trade."""
    t = trader({"UNIUSDT": _sym(IDLE), "LDOUSDT": _sym(TRAILING)})
    t.daily_freeze = False
    # Mid-session, no freeze: the live coin may enter, the wind-down coin may not.
    assert t._blocks_new_entries("UNIUSDT", in_session=True) is False
    assert t._blocks_new_entries("LDOUSDT", in_session=True) is True
    # And the other two reasons still apply to everyone.
    assert t._blocks_new_entries("UNIUSDT", in_session=False) is True
    t.daily_freeze = True
    assert t._blocks_new_entries("UNIUSDT", in_session=True) is True


def test_flat_winddown_symbol_leaves_the_book(trader):
    """Once the position closes the symbol should be gone, not linger forever."""
    t = trader({"LDOUSDT": _sym(SCALE_OPEN)})
    assert "LDOUSDT" in t.winddown
    t.sym_states["LDOUSDT"].state = IDLE      # simulate the exit firing
    # The bar loop drops it on the next pass; reproduce that step.
    for sym in list(t.winddown):
        if t.sym_states[sym].state == IDLE:
            t.winddown.discard(sym)
            t.sym_states.pop(sym, None)
    assert t.winddown == set()
    assert "LDOUSDT" not in t.sym_states


def test_current_universe_is_untouched_by_the_adoption(trader):
    t = trader({"UNIUSDT": _sym(SCALE_OPEN), "LDOUSDT": _sym(SCALE_OPEN)})
    assert "UNIUSDT" not in t.winddown, "a live coin must never be wound down"
    assert t.sym_states["UNIUSDT"].state == SCALE_OPEN
