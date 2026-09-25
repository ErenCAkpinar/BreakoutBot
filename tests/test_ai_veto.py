"""H15.2 — the veto hook in strategy.py and the twin's reviewer. No network, no model."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from ai_shadow import live as LV
from ai_shadow.llm import Outcome
from config import COOLDOWN_BARS
from strategy import IDLE, SCALE_OPEN, TEST_OPEN, SymbolState

ROOT = Path(__file__).resolve().parents[1]


def probe_about_to_confirm() -> SymbolState:
    s = SymbolState(symbol="UNIUSDT")
    s.state, s.direction = TEST_OPEN, "LONG"
    s.test_entry, s.test_atr, s.test_sl = 100.0, 1.0, 99.0
    return s


def resolve(s: SymbolState, **kw: Any):
    # price 101 > +0.05%, rsi 60 ≥ 45, volume 1.5× — confirms; low 100.5 keeps the probe SL untouched.
    return s._resolve_probe(101.0, 101.5, 100.5, 60.0, 1.5, kw.pop("block", False),
                            kw.pop("size_mult", 1.0), **kw)


def state_of(s: SymbolState) -> dict[str, Any]:
    d = asdict(s)
    d.pop("engine", None)
    return d


# ── strategy hook ─────────────────────────────────────────────────────────────

def test_a_veto_that_allows_changes_nothing():
    base, allowed = probe_about_to_confirm(), probe_about_to_confirm()
    ev_base = resolve(base)
    ev_allowed = resolve(allowed, veto=lambda p: False)
    assert ev_base == ev_allowed and state_of(base) == state_of(allowed)
    assert base.state == SCALE_OPEN and [e.exit_type for e in ev_base] == ["CONFIRM_OK", "OPEN"]


def test_the_veto_sees_exactly_the_levels_that_would_open():
    seen: list[dict] = []
    opened = probe_about_to_confirm()
    resolve(opened)
    resolve(probe_about_to_confirm(), veto=lambda p: seen.append(p) or False)
    p = seen[0]
    assert (p["full_entry"], p["full_sl"], p["full_tp1"], p["full_tp2"], p["full_notional"]) == \
        (opened.full_entry, opened.full_sl, opened.full_tp1, opened.full_tp2, opened.full_notional)
    assert p["symbol"] == "UNIUSDT" and p["direction"] == "LONG" and p["test_atr"] == 1.0


def test_a_veto_suppresses_the_full_like_max_open_does():
    vetoed, blocked = probe_about_to_confirm(), probe_about_to_confirm()
    ev_vetoed = resolve(vetoed, veto=lambda p: True)
    ev_blocked = resolve(blocked, block=True)
    assert ev_vetoed == ev_blocked                       # the probe still closes CONFIRM_OK
    assert [e.exit_type for e in ev_vetoed] == ["CONFIRM_OK"]
    assert vetoed.state == IDLE and vetoed.cooldown == COOLDOWN_BARS
    assert state_of(vetoed) == state_of(blocked)


@pytest.mark.parametrize("kw", [{"block": True}, {"size_mult": 0.0}])
def test_the_veto_is_not_asked_when_nothing_would_open(kw):
    calls: list[dict] = []
    resolve(probe_about_to_confirm(), veto=lambda p: calls.append(p) or True, **kw)
    assert calls == []


def test_the_veto_is_not_asked_when_confirmation_fails():
    calls: list[dict] = []
    s = probe_about_to_confirm()
    s._resolve_probe(100.01, 100.2, 99.9, 60.0, 1.5, False, 1.0,
                     veto=lambda p: calls.append(p) or True)   # +0.01% < the 0.05% needed
    assert calls == [] and s.state == IDLE


# ── the flag-off bot never loads the reviewer ─────────────────────────────────

def test_flag_off_bot_does_not_import_the_reviewer_or_the_sdk():
    code = ("import sys, paper_bb; "
            "print(any(m.startswith('ai_shadow') for m in sys.modules), 'anthropic' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin"}, check=True).stdout.split()
    assert out == ["False", "False"]


# ── LiveVeto ──────────────────────────────────────────────────────────────────

BAR = datetime(2026, 9, 25, 10, 35, tzinfo=timezone.utc)
PROSPECTIVE = {"symbol": "UNIUSDT", "direction": "LONG", "test_entry": 9.356, "test_atr": 0.046,
               "full_entry": 9.377, "full_notional": 452.35, "full_sl": 9.2734,
               "full_tp1": 9.5152, "full_tp2": 9.6534}


def make(tmp_path: Path, monkeypatch, outcome: Outcome | None = None,
         build_raises: Exception | None = None, seconds_after_bar: float = 40, budget: float = 8.0):
    logs: list[str] = []
    calls: list[float] = []

    def fake_build(cand, state, fetch):
        if build_raises is not None:
            raise build_raises
        assert state["sym_states"]["UNIUSDT"]["full_entry"] == cand.entry
        return "input text", {"input_sha256": "x" * 64, "input_chars": 10, "levels_available": True}

    def fake_decide(client, **kw):
        return outcome

    monkeypatch.setattr(LV, "build_input", fake_build)
    monkeypatch.setattr(LV, "decide", fake_decide)
    def client_factory(timeout: float) -> object:
        calls.append(timeout)
        return object()

    veto = LV.LiveVeto(tmp_path / "ai_decisions.jsonl", model="claude-opus-5-5", effort="medium",
                       budget_usd=budget, timeout_s=60, bar_deadline_s=200, log=logs.append,
                       client_factory=client_factory,
                       clock=lambda: BAR.timestamp() + seconds_after_bar)
    return veto, logs, calls


def decided(rec: str) -> Outcome:
    return Outcome("decided", decision={"recommendation": rec, "confidence": 0.6,
                                        "key_factors": [], "reasoning": ""},
                   input_tokens=14_000, output_tokens=1_700, cost_usd=0.09)


def last_record(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "ai_decisions.jsonl").read_text().splitlines()[-1])


def test_a_clean_veto_is_applied_and_recorded(tmp_path, monkeypatch):
    veto, logs, calls = make(tmp_path, monkeypatch, decided("VETO_RECOMMENDED"))
    assert veto.review(PROSPECTIVE, bar_dt=BAR, trade_log=[], balance=950.0) is True
    rec = last_record(tmp_path)
    assert rec["mode"] == "active" and rec["applied"] == "VETO" and rec["signal_id"] == \
        "UNIUSDT@2026-09-25T10:35:00Z"
    assert calls == [60] and "AI VETO" in logs[-1]


def test_allow_lets_the_entry_through(tmp_path, monkeypatch):
    veto, logs, _ = make(tmp_path, monkeypatch, decided("ALLOW"))
    assert veto.review(PROSPECTIVE, bar_dt=BAR, trade_log=[], balance=950.0) is False
    assert last_record(tmp_path)["applied"] == "ALLOW" and "AI ALLOW" in logs[-1]


@pytest.mark.parametrize("kwargs,reason", [
    ({"outcome": Outcome("no_data", "timeout")}, "timeout"),
    ({"build_raises": OSError("binance down")}, "market_data_OSError"),
    ({"seconds_after_bar": 150}, "bar_time_budget"),     # 200 − 150 < 60 + 5
    ({"budget": 0.1}, "budget"),
])
def test_every_miss_fails_open(tmp_path, monkeypatch, kwargs, reason):
    veto, logs, calls = make(tmp_path, monkeypatch, **kwargs)
    assert veto.review(PROSPECTIVE, bar_dt=BAR, trade_log=[], balance=950.0) is False
    rec = last_record(tmp_path)
    assert rec["no_data_reason"] == reason and rec["applied"] == "ALLOW"
    assert "fail-open" in logs[-1]
    if reason in ("bar_time_budget", "budget", "market_data_OSError"):
        assert calls == []


def test_an_unexpected_error_never_reaches_the_trading_loop(tmp_path, monkeypatch):
    veto, logs, _ = make(tmp_path, monkeypatch, decided("VETO_RECOMMENDED"))
    assert veto.review({"symbol": "UNIUSDT"}, bar_dt=BAR, trade_log=[], balance=950.0) is False
    assert "AI ERROR" in logs[-1]


def test_a_call_only_starts_with_a_full_timeout_left_before_the_deadline(tmp_path, monkeypatch):
    veto, _, calls = make(tmp_path, monkeypatch, decided("ALLOW"), seconds_after_bar=135)
    veto.review(PROSPECTIVE, bar_dt=BAR, trade_log=[], balance=950.0)
    assert calls == [60]                                 # 200 − 135 = 65 ≥ 60 + 5
    veto2, _, calls2 = make(tmp_path, monkeypatch, decided("ALLOW"), seconds_after_bar=136)
    veto2.review(PROSPECTIVE, bar_dt=BAR, trade_log=[], balance=950.0)
    assert calls2 == []                                  # 64 < 65: the bar loop is not held past it
