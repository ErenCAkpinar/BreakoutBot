"""Parity tests: the backtest must gate a bar the way the live bot gates it.

Every assertion here corresponds to a divergence that was actually present in
backtest.py and silently changed the results:

  · momentum probes fired in NEUTRAL/BEAR, where live never scans at all
  · MAX_OPEN was never enforced, so the book took trades it had no slot for
  · MR opened on a symbol that already had a momentum position (netting)
  · the equity throttle did not exist
  · a probe stop-out was logged as a MOMENTUM "SL" leg and could close an
    unrelated pending TP1
  · probe legs never reached the position set at all, so backtest expectancy
    excluded a cost the live record has always carried

The sleeves are stubbed on purpose: what is under test is the gate, not the
strategy behind it. A stub makes "was it even called?" observable, which is
exactly the question the NEUTRAL-probe bug turned on.
"""
from __future__ import annotations

from datetime import timezone

import backtest
from backtest import (_bar_close_dt, _mom_leg, account_events, new_tally,
                      process_symbol_bar)
from backtest_report import window_line, print_portfolio_report
from config import MAX_OPEN
from metrics import aggregate_positions
from strategy import IDLE, SCALE_OPEN, TEST_OPEN, Trade


class _SpySym:
    """Stands in for strategy.SymbolState; records whether it was consulted."""
    def __init__(self, state: str = IDLE) -> None:
        self.state = state
        self.calls: list[dict] = []

    def process_bar(self, snap, high, low, rsi_val, vol_ratio,
                    block_new_full=False, size_mult=1.0):
        self.calls.append({"block_new_full": block_new_full,
                           "size_mult": size_mult})
        return []


class _SpyMR:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def process_bar(self, snap, high, low, regime="NEUTRAL",
                    block_new=False, size_mult=1.0):
        self.calls.append({"block_new": block_new, "size_mult": size_mult})
        return []


def _run(state=IDLE, reg="BULL", size_factor=1.0, block_new=False, open_full=0,
         mr_enabled=None, monkeypatch=None):
    """Drive one symbol-bar through the gate sequence.

    `mr_enabled` forces config.MR_ENABLED for the call. The sleeve is OFF by
    default since 2026-08-27 (arm R1-mr-off), so a test that asserts anything
    about MR's gating has to switch it on and say so — otherwise it silently
    asserts against a sleeve that was never invoked, and passes for the wrong
    reason the day someone turns it back on.
    """
    if mr_enabled is not None:
        assert monkeypatch is not None, "mr_enabled needs the monkeypatch fixture"
        monkeypatch.setattr(backtest, "MR_ENABLED", mr_enabled)
    sym, mr = _SpySym(state), _SpyMR()
    process_symbol_bar(sym, mr, {}, 1.0, 1.0,   # type: ignore[arg-type]
                       reg=reg, size_factor=size_factor, block_new=block_new,
                       open_full=open_full, rsi_val=50.0, vol_ratio=1.0)
    return sym, mr


# ── The regime gate: no probes outside BULL ──────────────────────────────────

def test_idle_symbol_is_not_scanned_outside_bull():
    """PaperTrader._run_momentum_sleeve — live skips the symbol entirely when
    size_mult <= 0.

    LONG_SIZE_MULT is {BULL: 1.0, NEUTRAL: 0.0, BEAR: 0.0}. The backtest used to
    scan anyway: it opened a $20 probe, paid both sides of the fee, sometimes ate a
    probe SL, parked the symbol in a 10-bar cooldown — and then refused the full
    position in SymbolState.process_bar because size_mult was 0. Pure fabricated drag on
    every non-BULL bar, which is most of them.
    """
    for reg in ("NEUTRAL", "BEAR"):
        sym, _ = _run(reg=reg)
        assert sym.calls == [], f"{reg}: IDLE symbol was scanned"


def test_idle_symbol_is_scanned_in_bull():
    sym, _ = _run(reg="BULL")
    assert len(sym.calls) == 1
    assert sym.calls[0]["size_mult"] == 1.0


def test_open_position_is_still_managed_outside_bull():
    """The gate blocks NEW entries only — an open position must keep being managed
    even after the regime flips, or its stop never fires."""
    sym, _ = _run(state=SCALE_OPEN, reg="BEAR", block_new=True)
    assert len(sym.calls) == 1


def test_equity_throttle_halves_size(monkeypatch):
    """PaperTrader._apply_equity_throttle — a -7% peak drawdown halves sizing
    on both sleeves."""
    sym, mr = _run(reg="BULL", size_factor=0.5,
                   mr_enabled=True, monkeypatch=monkeypatch)
    assert sym.calls[0]["size_mult"] == 0.5
    assert mr.calls[0]["size_mult"] == 0.5


def test_mr_sleeve_is_not_invoked_when_disabled(monkeypatch):
    """The adopted default (2026-08-27) is MR_ENABLED=False, and "off" has to mean
    the sleeve is never consulted — not merely that it declines to open. A sleeve
    that still runs its scan would keep paying its cooldown and state bookkeeping
    for nothing."""
    _, mr = _run(reg="NEUTRAL", mr_enabled=False, monkeypatch=monkeypatch)
    assert mr.calls == []


# ── The portfolio cap ────────────────────────────────────────────────────────

def test_max_open_blocks_the_scale_up():
    """PaperTrader._run_momentum_sleeve — a confirmed probe is dropped when the
    book is full."""
    sym, _ = _run(state=TEST_OPEN, reg="BULL", open_full=MAX_OPEN)
    assert sym.calls[0]["block_new_full"] is True

    sym, _ = _run(state=TEST_OPEN, reg="BULL", open_full=MAX_OPEN - 1)
    assert sym.calls[0]["block_new_full"] is False


# ── The MR netting guard ─────────────────────────────────────────────────────

def test_mr_is_blocked_while_momentum_holds_the_symbol(monkeypatch):
    """PaperTrader._run_mr_sleeve — the exchange nets two longs on one symbol
    into a single
    position, so an MR close would shut the momentum leg with it.

    Asserted with the sleeve forced ON: this is the netting contract that must
    still hold whenever MR is re-enabled for research."""
    _, mr = _run(state=SCALE_OPEN, reg="NEUTRAL",
                 mr_enabled=True, monkeypatch=monkeypatch)
    assert mr.calls[0]["block_new"] is True

    _, mr = _run(state=IDLE, reg="NEUTRAL",
                 mr_enabled=True, monkeypatch=monkeypatch)
    assert mr.calls[0]["block_new"] is False


# ── Leg accounting ───────────────────────────────────────────────────────────

def test_probe_stop_out_is_tagged_probe_sl():
    """PaperTrader._book_probe_result. Left as "SL", metrics reads a probe stop as a MOMENTUM
    final leg — and it would close whatever TP1 was pending on that symbol."""
    ev = Trade(symbol="UNIUSDT", direction="LONG", kind="TEST",
               entry=10.0, exit=9.9, pnl=-0.22, exit_type="SL")
    assert _mom_leg(ev)["exit_type"] == "PROBE_SL"
    assert aggregate_positions([_mom_leg(ev)])[0].sleeve == "PROBE"


def test_every_leg_reaches_the_log_and_reconciles():
    """T0: sum(legs.pnl) must equal the balance delta, OPEN fees included."""
    mom = [
        Trade("UNIUSDT", "LONG", "TEST", 10.0, 0.0, -0.015, "OPEN"),
        Trade("UNIUSDT", "LONG", "TEST", 10.0, 10.1, -0.030, "CONFIRM_OK"),
        Trade("UNIUSDT", "LONG", "FULL", 10.1, 0.0, -0.675, "OPEN"),
        Trade("UNIUSDT", "LONG", "FULL", 10.1, 11.0, 21.000, "TP2"),
    ]
    mr = [
        Trade("ADAUSDT", "LONG", "MR", 0.5, 0.0, -0.150, "OPEN"),
        Trade("ADAUSDT", "LONG", "MR", 0.5, 0.52, 5.000, "TP"),
    ]
    legs: list[dict] = []
    tally = new_tally()
    delta = account_events(mom, mr, legs, tally)

    assert len(legs) == 6
    assert abs(sum(x["pnl"] for x in legs) - delta) < 1e-9
    assert tally["probe"] == 1 and tally["full"] == 1
    assert tally["confirm_ok"] == 1 and tally["TP2"] == 1 and tally["MR_TP"] == 1

    # The probe leg is a position in its own sleeve — the live record counts it.
    sleeves = sorted(p.sleeve for p in aggregate_positions(legs))
    assert sleeves == ["MOMENTUM", "MR", "PROBE"]


def test_probe_drag_counts_only_positions_that_never_opened():
    """A CONFIRM_OK rolled into a real position; its cost belongs to that
    position, not to the filter's drag (paper_bb tracks the same split)."""
    legs: list[dict] = []
    tally = new_tally()
    account_events([
        Trade("UNIUSDT", "LONG", "TEST", 10.0, 9.99, -0.05, "CONFIRM_FAIL"),
        Trade("ADAUSDT", "LONG", "TEST", 0.5, 0.49, -0.22, "SL"),
        Trade("INJUSDT", "LONG", "TEST", 20.0, 20.1, -0.03, "CONFIRM_OK"),
    ], [], legs, tally)
    assert abs(tally["probe_cost"] - (-0.27)) < 1e-9


# ── Bar clock ────────────────────────────────────────────────────────────────

def test_bar_timestamp_is_the_close_not_the_open():
    """Binance stamps a candle with its OPEN time; the live bot acts on it at the
    boundary it CLOSED on, so the session filter and the daily reset key off close
    time. Off by one bar, every session edge and every day rollover moves."""
    open_ms = 1_700_000_000_000 - (1_700_000_000_000 % 300_000)
    dt = _bar_close_dt(open_ms)
    assert dt.tzinfo == timezone.utc
    assert dt.timestamp() * 1000 == open_ms + 300_000


def test_hurst_is_exact_by_default():
    """Hurst is an entry gate (HURST_LONG_MIN). The backtest used to reuse a value
    up to 4 bars stale, which opens and blocks different trades than live."""
    assert backtest._HURST_STRIDE == 1


# ── Truncated runs must never be reported as complete ────────────────────────

def _halted_r(**over):
    """A result dict shaped like run_portfolio returns, good stats, halted early."""
    r = {
        "symbols": ["UNIUSDT"], "days": 2095, "days_run": 87.0, "bars": 25255,
        "halted": True, "n_halts": 1, "start_ts": 1606521600000,
        "end_ts": 1614124800000, "start_balance": 1000.0, "final_balance": 936.48,
        "total_return": -6.35, "monthly_est": -2.19, "max_dd": -15.42,
        "recon_err": 0.0, "entry_fees": -96.87, "n_positions": 1151,
        "regime_counts": {"BULL": 97, "NEUTRAL": 3, "BEAR": 0},
        "tally": {"probe": 982, "full": 167, "confirm_ok": 167, "confirm_fail": 688,
                  "maxopen_block": 4, "probe_cost": -32.76, "TP1": 0, "TP2": 38,
                  "SL": 85, "TRAIL": 41, "TIMEOUT": 3, "MR_TP": 0, "MR_SL": 2,
                  "MR_TIMEOUT": 0},
        # deliberately PASSING stats: the halt alone must veto the verdict
        "pos_stats": {"n_positions": 1151, "win_rate": 44.0, "breakeven_wr": 43.0,
                      "avg_win": 1.50, "avg_loss": -1.13, "payoff": 1.33,
                      "expectancy": 0.64, "expectancy_r": 0.064,
                      "profit_factor": 1.76, "by_sleeve": {}},
        "pos_exits": {"SL": 87}, "_positions": [], "_legs": [], "coverage": {},
    }
    r.update(over)
    return r


def test_halted_run_reports_the_window_it_actually_replayed():
    """The 2095d run replayed 87 days, hit PEAK_DD_LIMIT, and printed a monthly
    estimate for 2095 days it never saw. The requested span is not the result."""
    line = window_line(_halted_r())
    assert "87d REPLAYED" in line
    assert "hard-stopped" in line.lower()
    assert "2008d were never traded" in line     # 2095 - 87


def test_halted_run_can_never_print_deploy(capsys):
    """Stats that would otherwise pass must not produce a DEPLOY verdict on a
    window the run never finished."""
    print_portfolio_report(_halted_r())
    out = capsys.readouterr().out
    assert "DEPLOY" not in out
    assert "HARD-STOPPED" in out


def test_restart_mode_is_flagged_as_not_parity():
    line = window_line(_halted_r(halted=False, n_halts=3, days_run=2095.0))
    assert "3 hard stop(s)" in line
    assert "not live parity" in line


def test_short_data_is_flagged_not_silently_scaled():
    """A coin listed mid-window replays fewer days than asked; the rate metrics
    must be scored on what was replayed, and the report must say so."""
    line = window_line(_halted_r(halted=False, n_halts=0, days_run=710.0))
    assert "710d replayed" in line and "data starts later" in line


def test_full_run_reports_plainly():
    line = window_line(_halted_r(halted=False, n_halts=0, days_run=2095.0))
    assert line == "2095d replayed"
