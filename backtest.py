"""
BreakoutBot Backtest

Replays the deployed system on historical 5m OHLCV data.

Two modes:

  portfolio (default) — ONE shared account across all tokens, bar-synchronised,
      with MAX_OPEN, the equity throttle and the drawdown guards applied to the
      book. This is what paper_bb.py does live, and it is the only mode whose
      numbers describe the real account.

  --per-symbol        — one independent account per token, no portfolio cap.
      Useful for RANKING coins against each other (that is what the curation in
      config.TOKENS was built on); it is not a picture of the deployed system.

Every gate in both modes mirrors paper_bb.PaperTrader._process_bar — see the
PARITY CONTRACT block below, which cites the live line for each one.

Usage:
    python3.12 backtest.py                          # live 5-coin book, 90d
    python3.12 backtest.py --days 240               # deeper window
    python3.12 backtest.py --per-symbol --beta      # per-coin ranking + beta
    python3.12 backtest.py --all --days 240         # full 23-coin universe
    python3.12 backtest.py --cache                  # pin/reuse the window on disk
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from indicators import build_snapshot, hurst_exponent, precompute_indicators
from strategy   import SymbolState, IDLE, TEST_OPEN, SCALE_OPEN, TRAILING
from mean_reversion import MRState
from short_sleeve import ShortState
import regime
from config import MR_ENABLED, SHORT_ENABLED
from config import (
    TOKENS, INITIAL_BALANCE,
    DAILY_DD_LIMIT, EQUITY_THROTTLE_DD, PEAK_DD_LIMIT, DAILY_SL_LIMIT,
    SESSION_START_UTC, SESSION_END_UTC,
    MAX_OPEN, RISK_PER_TRADE_USD,
    BTC_GATE_ENABLED,
)
from metrics import aggregate_positions, position_stats, exit_distribution
from config import REGIME_WARMUP_DAYS, REGIME_WARMUP_BARS, RISK_BY_SLEEVE
from config import _FULL_UNIVERSE
# Data access and report formatting moved to their own modules; imported BY NAME
# so `backtest.fetch_history` and friends still resolve for existing callers and
# for the research harnesses that patch this module's globals.
from backtest_data import CACHE_DIR, fetch_history, load_frames
from backtest_report import (dump_run, print_beta_ranking,
                             print_portfolio_report, print_progress,
                             print_report, sleeve_line)


# ── Data fetch ────────────────────────────────────────────────────────────────




# ── Live-parity core ──────────────────────────────────────────────────────────
#
# PARITY CONTRACT
# ---------------
# paper_bb.PaperTrader._process_bar is the reference implementation — it is the
# code actually running the account. Everything in this section reproduces its
# gate ORDER and its gate CONDITIONS exactly, with the mirrored METHOD cited per
# gate. Methods, not line numbers: this contract used to cite lines, and every
# one of them had drifted onto an unrelated statement by 2026-09-22. A change on either side that is not mirrored here turns every backtest
# number into a fiction, which is the whole failure mode this section exists to
# prevent.
#
# Differences that CANNOT be reproduced offline (and are immaterial):
#   · live re-fetches a rolling 200-bar frame per symbol per bar, so its EWM
#     indicators warm from 200 bars while the backtest warms from the whole
#     window. Every EWM span here is <= 50, so the two converge well inside the
#     65-bar warm-up.
#   · live drops the still-forming candle (paper_bb.fetch_recent); the backtest
#     only ever sees closed bars. Same thing by construction.
#   · live can miss a bar on a fetch error and skip that symbol. The backtest
#     skips a symbol whose frame has no row for the bar — same handling.
#
# Difference that is REAL and deliberate: the short sleeve. paper_bb does not
# import short_sleeve at all, so the live bot cannot take a short. run_symbol
# still runs it when config.SHORT_ENABLED is on (research path) and warns loudly.

# ── Exit parameters actually deployed ─────────────────────────────────────────
# These used to live ONLY in the systemd unit as env overrides while config.py
# defaulted to something else, so a bare `python3.12 backtest.py` silently tested
# a different exit structure than the account was running. As of 2026-08-27 the
# validated set (arm R1-sl225t96) IS the config.py default, so this table exists
# to catch the reverse drift: a stale env still pinned in the unit.
#
# ⚠️ The unit is expected to carry `X_TP1_CLOSE_FRAC=0.0 X_TRAIL_ATR=2.5` from
#    the E6 era. X_TRAIL_ATR=2.5 now OVERRIDES the adopted 3.75 — that env has to
#    be removed from the unit, or the live bot runs a mix nothing validated.
LIVE_ENV = {
    "X_TP1_CLOSE_FRAC": 0.0,
    "X_TRAIL_ATR":      3.75,
    "X_SL_FULL_ATR":    2.25,
    "X_TP1_ATR":        3.0,
    "X_TP2_ATR":        6.0,
    "X_TIMEOUT_BARS":   96.0,
}


def check_live_params() -> None:
    """Print the exit params in force and flag any that differ from production."""
    import config as _cfg
    current = {"X_TP1_CLOSE_FRAC": _cfg.TP1_CLOSE_FRAC,
               "X_TRAIL_ATR":      _cfg.TRAIL_ATR,
               "X_SL_FULL_ATR":    _cfg.SL_FULL_ATR,
               "X_TP1_ATR":        _cfg.TP1_ATR,
               "X_TP2_ATR":        _cfg.TP2_ATR,
               "X_TIMEOUT_BARS":   float(_cfg.TIMEOUT_BARS)}
    drift = {k: (v, LIVE_ENV[k]) for k, v in current.items()
             if abs(v - LIVE_ENV[k]) > 1e-9}

    print(f"⚙️  Exits: TP1_CLOSE_FRAC={_cfg.TP1_CLOSE_FRAC:g} "
          f"TRAIL_ATR={_cfg.TRAIL_ATR:g} SL_FULL_ATR={_cfg.SL_FULL_ATR:g} "
          f"TP1_ATR={_cfg.TP1_ATR:g} TP2_ATR={_cfg.TP2_ATR:g} "
          f"TIMEOUT_BARS={_cfg.TIMEOUT_BARS}")
    print(f"   Risk: ${_cfg.RISK_PER_TRADE_USD:g}/trade  MAX_OPEN={_cfg.MAX_OPEN}  "
          f"MR={'on' if _cfg.MR_ENABLED else 'off'} (${_cfg.MR_RISK_PER_TRADE_USD:g})  "
          f"shorts={'ON — NOT LIVE' if _cfg.SHORT_ENABLED else 'off'}  "
          f"longs {regime.LONG_SIZE_MULT}")
    if drift:
        print("  ⚠️  These are NOT the deployed exits. The live bot runs:")
        for k, (have, want) in drift.items():
            print(f"        {k}={want:g}   (this run: {have:g})")
        print("      Re-run as:  "
              + " ".join(f"{k}={v:g}" for k, v in LIVE_ENV.items())
              + " python3.12 backtest.py …")
    else:
        print("  ✅ matches the deployed E6 exit set")


@dataclass
class _GateStep:
    """What the account-level gates decided for one bar."""
    halt:        str | None    # None · "peak_dd" · "bankrupt"
    size_factor: float
    block_new:   bool


@dataclass
class _RiskGates:
    """Account-level risk state, stepped once per bar before any sleeve runs.

    This is the backtest's copy of the gates in paper_bb.PaperTrader — see the
    PARITY CONTRACT above. It exists as one object because run_symbol and
    run_portfolio used to carry a character-identical copy of this block each:
    two places to keep in step with the live bot, and a fix could land in one
    and not the other.

    Gate order mirrors PaperTrader._process_bar exactly:
      _roll_daily_window → _guard_peak_drawdown → _apply_equity_throttle
      → _apply_daily_freeze → session filter
    """
    peak:           float
    daily_day:      str   = ""
    daily_start:    float = 0.0
    daily_freeze:   bool  = False
    daily_sl_count: int   = 0
    n_halts:        int   = 0

    def step(self, bal: float, day: str, hour: int) -> _GateStep:
        # ── Daily reset (PaperTrader._roll_daily_window) ──────────────────────
        if day != self.daily_day:
            self.daily_day      = day
            self.daily_start    = bal
            self.daily_freeze   = False
            self.daily_sl_count = 0

        # ── Peak DD hard stop (PaperTrader._guard_peak_drawdown, which sys.exits
        #    live — here the caller decides, and BT_RESTARTS models a restart) ──
        if bal > self.peak:
            self.peak = bal
        peak_dd = (bal - self.peak) / self.peak if self.peak > 0 else 0.0
        if peak_dd <= PEAK_DD_LIMIT:
            self.n_halts += 1
            if not _RESTARTS:
                return _GateStep("peak_dd", 1.0, True)
            self.peak = bal        # operator restarts: peak rebased, run continues
            peak_dd   = 0.0

        # Position size is a FIXED dollar risk, not a fraction of equity, so a
        # simulated account keeps trading at full size after the money is gone —
        # a 2095d --restarts run replayed 300k more bars at a balance of -$915.
        # A real account is liquidated at zero, so everything past that point is
        # fiction. The run ends here and says why.
        if bal <= 0:
            return _GateStep("bankrupt", 1.0, True)

        # ── Equity throttle (PaperTrader._apply_equity_throttle) ──────────────
        size_factor = 0.5 if peak_dd <= EQUITY_THROTTLE_DD else 1.0

        # ── Daily DD freeze (PaperTrader._apply_daily_freeze) ─────────────────
        intraday_dd = ((bal - self.daily_start) / self.daily_start
                       if self.daily_start > 0 else 0.0)
        if intraday_dd <= DAILY_DD_LIMIT and not self.daily_freeze:
            self.daily_freeze = True

        # ── Session filter (PaperTrader._blocks_new_entries) ──────────────────
        in_session = SESSION_START_UTC <= hour < SESSION_END_UTC
        return _GateStep(None, size_factor, self.daily_freeze or not in_session)

    def count_stop_loss(self, n_new: int) -> None:
        """Fold this bar's stop-outs in; DAILY_SL_LIMIT freezes the rest of the day.

        Live this is per-symbol (PaperTrader._book_full_close freezes the symbol);
        both replay modes freeze the whole book, which is the stricter reading.
        """
        self.daily_sl_count += n_new
        if self.daily_sl_count >= DAILY_SL_LIMIT:
            self.daily_freeze = True


# PEAK_DD_LIMIT ends a run: live, paper_bb calls sys.exit and a human decides
# whether to restart. A backtest cannot model that decision, so by default it
# stops too — and MUST then report the window it actually replayed, not the one
# that was requested. BT_RESTARTS=1 models "the operator restarts the bot after
# every hard stop" (peak resets to the current balance, the run continues) so a
# multi-year study is not truncated by the first bad quarter. It is NOT live
# parity: it assumes someone always restarts, which is itself a decision.
_RESTARTS     = os.getenv("BT_RESTARTS") == "1"
_NO_REGIME    = os.getenv("BT_NO_REGIME") == "1"           # ablation: no regime gate
_HURST_STRIDE = max(1, int(os.getenv("BT_HURST_STRIDE", "1")))


def _row_for_ts(ts_arr: np.ndarray, ts: int) -> int | None:
    """Row index of `ts` in a sorted ts column, or None when that bar is missing.

    Replaces a per-symbol {ts: row} dict. Those cost ~60 MB per 615k-bar frame,
    ~370 MB across six frames — most of the footprint that killed a 6-year
    portfolio run. A binary search on the array we already hold costs nothing
    next to the ~2.4 ms each symbol-bar spends in Hurst and the snapshot.
    """
    if ts_arr.size == 0:
        return None
    i = int(np.searchsorted(ts_arr, ts))
    if i >= ts_arr.size or int(ts_arr[i]) != ts:
        return None
    return i


def _bar_close_dt(ts_ms: int) -> datetime:
    """UTC time the 5m bar stamped `ts_ms` CLOSED at.

    Binance stamps a candle with its OPEN time. The live bot wakes on the bar
    boundary and processes the candle that just closed (paper_bb.wait_for_bar_close
    returns that boundary), so its session filter and its daily reset key off the
    CLOSE time. Keying off the open time shifts every session edge and every day
    rollover by one bar.
    """
    return datetime.fromtimestamp((ts_ms + 300_000) / 1000, tz=timezone.utc)


def _hurst_at(closes: pd.Series, i: int, cache: dict[int, float]) -> float:
    """Hurst over the 40 closes ending at bar `i`.

    Live recomputes this every single bar (PaperTrader._symbol_snapshot). The backtest used to
    reuse a value up to 4 bars stale, and Hurst is an ENTRY GATE
    (SymbolState.process_bar's HURST_LONG_MIN gate) — a stale value silently opens and blocks
    different trades than production. Stride is 1 (exact) by default;
    BT_HURST_STRIDE>1 buys speed on a sweep at the cost of that parity.
    """
    key = i - (i % _HURST_STRIDE)
    val = cache.get(key)
    if val is None:
        val = hurst_exponent(closes.iloc[max(0, key - 39): key + 1])
        cache[key] = val
    return val


def _leg(ev, sleeve: str, exit_type: str | None = None,
         ts: int | None = None) -> dict:
    """One balance-moving leg, shaped exactly like paper_bb._log_leg writes it.

    T0 — every leg that moves the balance must land here, OPEN legs included.
    Their pnl is the entry fee; dropping them is what made the live trade log
    disagree with the balance by -$33 and flipped the published expectancy's sign.
    aggregate_positions() ignores exit_type "OPEN", so logging them changes no
    position count — it only makes the money add up.
    """
    return {
        "ts":        ts,
        "symbol":    ev.symbol,
        "direction": ev.direction,
        "sleeve":    sleeve,
        "kind":      getattr(ev, "kind", ""),
        "exit_type": exit_type if exit_type is not None else ev.exit_type,
        "entry":     ev.entry,
        "exit":      ev.exit,
        "pnl":       ev.pnl,
    }


def _mom_leg(ev, ts: int | None = None) -> dict:
    """Route a momentum-sleeve event to its sleeve the way paper_bb does.

    A probe that stops out carries exit_type "SL". Left alone, metrics.py reads it
    as a MOMENTUM final leg and lets it close an unrelated pending TP1. The live
    bot renames it PROBE_SL (PaperTrader._book_probe_result); so does this.
    """
    if getattr(ev, "kind", "") == "TEST":
        return _leg(ev, "PROBE",
                    "PROBE_SL" if ev.exit_type == "SL" else ev.exit_type, ts)
    return _leg(ev, "MOMENTUM", None, ts)




def new_tally() -> dict:
    """Counters both backtest modes share (the live bot keeps them as attributes)."""
    return {
        "probe": 0, "full": 0, "confirm_ok": 0, "confirm_fail": 0,
        "maxopen_block": 0, "probe_cost": 0.0,
        "TP1": 0, "TP2": 0, "SL": 0, "TRAIL": 0, "TIMEOUT": 0,
        "MR_TP": 0, "MR_SL": 0, "MR_TIMEOUT": 0,
    }


def process_symbol_bar(sym_state: SymbolState, mr_state: MRState, snap: dict,
                       high: float, low: float, *,
                       reg: str, size_factor: float, block_new: bool,
                       open_full: int, rsi_val: float, vol_ratio: float
                       ) -> tuple[list, list, dict]:
    """One symbol, one bar — the exact gate sequence paper_bb._process_bar runs.

    Returns (momentum_events, mr_events, info).

    Gate-by-gate, against the live bot:

      size_mult   = LONG_SIZE_MULT[reg] * size_factor
                                                    PaperTrader._process_bar
                    LONG_SIZE_MULT is {BULL: 1.0, NEUTRAL: 0, BEAR: 0}, and the
                    default on an unknown label is 0.0 (live) — NOT 1.0.

      momentum    skipped entirely while IDLE if block_new OR size_mult <= 0
                                            PaperTrader._run_momentum_sleeve
                    This is the fix that matters most. The backtest used to gate
                    on block_new only, so in NEUTRAL/BEAR it opened $20 probes the
                    live bot never opens — paying two sides of fees, occasionally
                    a probe SL, and parking the symbol in a 10-bar cooldown — then
                    hit the size_mult>0 check in SymbolState.process_bar and refused the
                    full position anyway. Pure
                    manufactured drag, on ~all bars outside BULL.

      block_new_full = (state == TEST_OPEN and open_full >= MAX_OPEN)
                                            PaperTrader._run_momentum_sleeve
                    The portfolio cap. Never binds in a single-symbol run.

      MR          blocked by block_new OR an open momentum position on the same
                  symbol (the netting guard), sized by the equity throttle
                                                  PaperTrader._run_mr_sleeve
                    Evaluated AFTER momentum has processed the bar, so it sees the
                    post-momentum state — same as live.
    """
    mult      = 1.0 if _NO_REGIME else regime.LONG_SIZE_MULT.get(reg, 0.0)
    size_mult = mult * size_factor

    mom_events: list = []
    block_new_full = False
    if not (sym_state.state == IDLE and (block_new or size_mult <= 0)):
        block_new_full = (sym_state.state == TEST_OPEN and open_full >= MAX_OPEN)
        mom_events = sym_state.process_bar(snap, high, low, rsi_val, vol_ratio,
                                           block_new_full=block_new_full,
                                           size_mult=size_mult)

    mr_events: list = []
    if MR_ENABLED:
        mr_block  = block_new or (sym_state.state != IDLE)
        mr_events = mr_state.process_bar(snap, high, low, regime=reg,
                                         block_new=mr_block,
                                         size_mult=size_factor)

    return mom_events, mr_events, {"size_mult": size_mult,
                                   "block_new_full": block_new_full}


def account_events(mom_events: list, mr_events: list,
                   legs: list[dict], tally: dict,
                   ts: int | None = None) -> float:
    """Log every leg, bump the tally, return the balance delta for this bar.

    Both backtest modes go through this one function so they cannot drift apart,
    and every leg reaches `legs` so sum(legs.pnl) == balance change (T0).
    """
    delta = 0.0

    for ev in mom_events:
        delta += ev.pnl
        legs.append(_mom_leg(ev, ts))
        if ev.exit_type == "OPEN":
            tally["probe" if ev.kind == "TEST" else "full"] += 1
            continue
        if ev.kind == "TEST":
            # Probe outcomes. CONFIRM_OK rolls into a full position; the other two
            # are money paid for a position that never existed — the live bot
            # tracks that drag explicitly (paper_bb.probe_cost) because it was 54%
            # of the total loss on the live record.
            if ev.exit_type == "CONFIRM_OK":
                tally["confirm_ok"] += 1
            elif ev.exit_type == "CONFIRM_FAIL":
                tally["confirm_fail"] += 1
                tally["probe_cost"]  += ev.pnl
            elif ev.exit_type == "SL":
                tally["probe_cost"]  += ev.pnl
            continue
        tally[ev.exit_type] = tally.get(ev.exit_type, 0) + 1

    for ev in mr_events:
        delta += ev.pnl
        legs.append(_leg(ev, "MR", None, ts))
        if ev.exit_type == "OPEN":
            continue
        key = "MR_" + ev.exit_type
        tally[key] = tally.get(key, 0) + 1

    return delta

# ── Single-symbol backtest ────────────────────────────────────────────────────

def run_symbol(symbol: str, days: int, balance: float = INITIAL_BALANCE,
               btc_df: pd.DataFrame | None = None,
               df: pd.DataFrame | None = None,
               trade_start_idx: int | None = None) -> dict:
    """Run backtest for one symbol.

    df     : pre-fetched OHLCV DataFrame (skips network fetch if supplied).
             It SHOULD carry a REGIME_WARMUP_BARS prefix before the trading
             window — see `trade_start_idx`.
    btc_df : BTC reference data for macro gate + beta. Must span the same range
             as `df`, prefix included, or the regime labels go cold with it.
    trade_start_idx :
             Row index in `df` where the TRADING window begins. Everything
             before it is warm-up only: indicators and the 4h regime MA are
             computed across it, but no bar is traded and none of it reaches
             the metrics.

    M1 — why the prefix exists:
        regime.score_series_4h needs 210 closed 4h bars (~35d). Without them
        regime.py:82 sets score = 0.0 → NEUTRAL, and LONG_SIZE_MULT["NEUTRAL"]
        is 0.0, so momentum trades NOTHING for the first ~33 days of a window
        while the MR sleeve (gated to NEUTRAL) inherits the whole month.

        df rows:  [─── warm-up prefix ───|───── trading window ─────]
                  0                    trade_start_idx           len(df)
                  └ indicators + 4h regime MA warm here
                                       └ first bar that can open a position

        `days` stays the TRADING window length — it only feeds trades_per_day
        and monthly_est, which must not be diluted by the prefix.
    """
    if df is None:
        # Self-fetch: pull the window PLUS the regime warm-up prefix.
        df = fetch_history(symbol, days + REGIME_WARMUP_DAYS)
        trade_start_idx = REGIME_WARMUP_BARS
    elif trade_start_idx is None:
        print(f"  ⚠️  {symbol}: run_symbol got a pre-fetched frame with no "
              f"trade_start_idx — the 4h regime MA will be cold for its first "
              f"~{REGIME_WARMUP_DAYS}d and momentum will trade nothing there (M1).",
              flush=True)

    warmup  = max(65, int(trade_start_idx or 0))   # ≥65 bars for indicator warm-up
    if warmup >= len(df):
        raise ValueError(
            f"{symbol}: trade_start_idx={trade_start_idx} leaves no bars to trade "
            f"(len(df)={len(df)}). Fetch days + REGIME_WARMUP_DAYS."
        )

    # ── Vectorised bulk indicator pre-computation (major speedup) ─────────
    df = precompute_indicators(df)

    # ── FAZ 1: per-bar regime labels (BULL/NEUTRAL/BEAR) for long throttling ──
    regimes = regime.backtest_regimes(df, btc_df)

    # Build BTC timestamp → row-index lookup for fast alignment
    btc_ts_index: dict[int, int] = {}
    if btc_df is not None:
        btc_ts_index = {int(ts): i for i, ts in enumerate(btc_df["ts"])}

    # Hurst is recomputed EVERY bar (stride 1) to match live — see _hurst_at.
    hurst_cache: dict[int, float] = {}
    closes_arr = df["close"]

    if SHORT_ENABLED:
        print(f"  ⚠️  {symbol}: SHORT_ENABLED=1, but the live bot has NO short "
              f"sleeve (paper_bb never imports short_sleeve). This run is "
              f"research-only — it does not describe the deployed system.",
              flush=True)

    sym_state   = SymbolState(symbol=symbol)
    mr_state    = MRState(symbol=symbol)
    short_state = ShortState(symbol=symbol)

    bal   = balance
    gates = _RiskGates(peak=balance, daily_start=balance)

    legs: list[dict] = []       # every balance-moving leg, live trade_log shape
    short_trades: list = []     # short sleeve — outside the live-parity accounting
    tally          = new_tally()
    equity_curve   = [balance]
    regime_counts  = {"BULL": 0, "NEUTRAL": 0, "BEAR": 0}
    s_tp1 = s_tp2 = s_sl = s_trail = s_tmo = 0

    t0     = time.time()
    n_bar  = len(df) - warmup
    verbose_bars = n_bar > 20_000        # long window → report, else stay quiet
    halted   = False
    bankrupt = False
    bars_run = 0
    first_ts = last_ts = 0

    for i in range(warmup, len(df)):
        if verbose_bars and (i - warmup) % 5000 == 0 and i > warmup:
            print_progress(i - warmup, n_bar, t0, bal, f"  {symbol}")
        row   = df.iloc[i]
        high  = float(row["high"])
        low   = float(row["low"])
        ts    = int(row["ts"])
        dt    = _bar_close_dt(ts)        # live keys off the bar's CLOSE time
        day   = dt.strftime("%Y-%m-%d")
        hour  = dt.hour
        if not bars_run:
            first_ts = ts
        bars_run += 1
        last_ts   = ts

        # ── Account-level gates, in the live bot's order (see _RiskGates) ──
        gate = gates.step(bal, day, hour)
        if gate.halt:
            halted   = True
            bankrupt = gate.halt == "bankrupt"
            break
        size_factor = gate.size_factor
        block_new   = gate.block_new

        reg = regimes[i]
        if sym_state.state == IDLE:
            regime_counts[reg] = regime_counts.get(reg, 0) + 1

        snap = build_snapshot(df, i, symbol, btc_df=btc_df,
                              hurst_override=_hurst_at(closes_arr, i, hurst_cache),
                              btc_row=btc_ts_index.get(ts))

        # open_full=0: a single-symbol run holds at most ONE full position and
        # MAX_OPEN is 2, so the portfolio cap can never bind here. It does bind in
        # run_portfolio — the only mode that can measure it.
        mom_events, mr_events, _info = process_symbol_bar(
            sym_state, mr_state, snap, high, low,
            reg=reg, size_factor=size_factor, block_new=block_new, open_full=0,
            rsi_val=snap["indicators"]["rsi_14"],
            vol_ratio=snap["volume"]["volume_ratio"])

        sl_before = tally["SL"]
        bal      += account_events(mom_events, mr_events, legs, tally, ts)
        gates.count_stop_loss(tally["SL"] - sl_before)

        # ── FAZ 3: short sleeve — NOT part of the live system ──────────────
        if SHORT_ENABLED:
            for ev in short_state.process_bar(snap, high, low,
                                              regime=reg, block_new=block_new):
                bal += ev.pnl
                if ev.exit_type == "OPEN":
                    continue
                short_trades.append(ev)
                if ev.exit_type == "TP1":
                    s_tp1 += 1
                elif ev.exit_type == "TP2":
                    s_tp2 += 1
                elif ev.exit_type == "SL":
                    s_sl += 1
                elif ev.exit_type == "TRAIL":
                    s_trail += 1
                elif ev.exit_type == "TIMEOUT":
                    s_tmo += 1

        equity_curve.append(bal)

    if verbose_bars and sys.stdout.isatty():
        print(flush=True)          # close the progress line

    # ── Metrics ───────────────────────────────────────────────────────────────
    # Positions are folded from the LEG LOG, exactly the way the live bot computes
    # them (paper_bb._print_status → aggregate_positions(trade_log)). That means
    # probe legs count as PROBE positions here too: they are real closed
    # round-trips with real cost. The backtest used to drop them, which is one
    # reason its expectancy read better than the account's.
    positions = aggregate_positions(legs)
    pos_stats = position_stats(positions, risk_per_trade=RISK_PER_TRADE_USD,
                               risk_by_sleeve=RISK_BY_SLEEVE)
    entry_fees = sum(leg["pnl"] for leg in legs if leg["exit_type"] == "OPEN")

    # T0 identity: every leg that moved the balance is in the log.
    recon_err = ((bal - balance) - sum(leg["pnl"] for leg in legs)
                 - sum(t.pnl for t in short_trades))
    if abs(recon_err) > 0.01:
        print(f"  ⚠️  {symbol}: leg log does not reconcile with the equity curve "
              f"(off by ${recon_err:+.4f}) — a balance-moving leg is missing.",
              flush=True)

    full_closed = [leg for leg in legs
                   if leg["sleeve"] == "MOMENTUM" and leg["exit_type"] != "OPEN"]
    mr_closed   = [leg for leg in legs
                   if leg["sleeve"] == "MR" and leg["exit_type"] != "OPEN"]
    closed_pnls = ([leg["pnl"] for leg in full_closed] + [leg["pnl"] for leg in mr_closed]
                   + [t.pnl for t in short_trades])

    n    = len(closed_pnls)
    wins = sum(1 for p in closed_pnls if p > 0)
    wr   = wins / n * 100 if n > 0 else 0.0

    # PEAK_DD_LIMIT ends the loop mid-window. Scoring a truncated run against the
    # REQUESTED length is how a coin that lost 15% in three months got reported as
    # "-15% over 665d, -0.68%/month" — a slow bleed, when it was a fast one. Rate
    # metrics use the window that was actually replayed.
    days_run = ((last_ts - first_ts) / 86_400_000) if bars_run > 1 else 0.0
    days_eff = days_run if days_run > 0.5 else float(days)
    tpd      = n / days_eff

    total_return = (bal - balance) / balance * 100
    avg_pnl      = (bal - balance) / n if n > 0 else 0.0

    eq    = np.array(equity_curve)
    pk    = np.maximum.accumulate(eq)
    maxdd = float(((eq - pk) / pk).min()) * 100

    gross_w = sum(p for p in closed_pnls if p > 0)
    gross_l = abs(sum(p for p in closed_pnls if p < 0))
    pf      = gross_w / gross_l if gross_l > 0 else float("inf")

    return {
        "symbol":          symbol,
        "days":            days,
        "trades":          n,
        "trades_per_day":  tpd,
        "win_rate":        wr,
        "profit_factor":   pf,
        "pos_stats":       pos_stats,
        "pos_exits":       exit_distribution(positions),
        "_positions":      positions,   # pooled cross-symbol stats (not printed)
        "_legs":           legs,
        "recon_err":       recon_err,
        "entry_fees":      entry_fees,
        "total_return":    total_return,
        "start_balance":   balance,
        "final_balance":   bal,
        "max_dd":          maxdd,
        "avg_pnl":         avg_pnl,
        "monthly_est":     total_return / days_eff * 30,
        "days_run":        days_run,
        "bars_run":        bars_run,
        "halted":          halted,
        "bankrupt":        bankrupt,
        "n_halts":         gates.n_halts,
        "first_ts":        first_ts,
        "last_ts":         last_ts,
        "tp1":             tally["TP1"],
        "tp2":             tally["TP2"],
        "sl":              tally["SL"],
        "trail":           tally["TRAIL"],
        "timeout":         tally["TIMEOUT"],
        "confirm_ok":      tally["confirm_ok"],
        "confirm_fail":    tally["confirm_fail"],
        "probe":           tally["probe"],
        "probe_cost":      tally["probe_cost"],
        "btc_gate_blocks": 0,           # obsolete — the regime gate replaced it
        "regime_counts":   regime_counts,
        "trades_full":     len(full_closed),
        "trades_mr":       len(mr_closed),
        "n_long":          sum(1 for leg in full_closed if leg["direction"] == "LONG"),
        "n_short":         len(short_trades),
        "short_wins":      sum(1 for t in short_trades if t.pnl > 0),
        "short_pnl":       sum(t.pnl for t in short_trades),
        "s_tp1":           s_tp1,
        "s_tp2":           s_tp2,
        "s_sl":            s_sl,
        "s_trail":         s_trail,
        "s_tmo":           s_tmo,
        "mr_tp":           tally["MR_TP"],
        "mr_sl":           tally["MR_SL"],
        "mr_tmo":          tally["MR_TIMEOUT"],
        "mr_wins":         sum(1 for leg in mr_closed if leg["pnl"] > 0),
        "tally":           tally,
        "_df":             df,      # keep fetched data for beta reuse (not printed)
    }




# ── Portfolio backtest (the live-parity mode) ─────────────────────────────────

def run_portfolio(tokens: list[str], days: int, balance: float = INITIAL_BALANCE,
                  *, data: dict, btc_df: "pd.DataFrame | None",
                  trade_start_idx: int) -> dict:
    """All tokens, ONE shared account, bar-synchronised — what production does.

    run_symbol() gives every token its own $1000 and no portfolio cap. That is a
    useful way to RANK coins, but it is not the deployed system and it flatters it
    in three specific ways:

      · MAX_OPEN never binds. Live holds at most 2 full positions across the whole
        book (config.MAX_OPEN); a confirmed signal on a third coin is dropped
        (PaperTrader._run_momentum_sleeve). Per-symbol runs take every one of them.
      · the drawdown guards never see the book. DAILY_DD_LIMIT, EQUITY_THROTTLE_DD
        and PEAK_DD_LIMIT are measured on ONE account live — five coins losing
        together throttle and then halt each other. Split across five independent
        accounts, the same losses barely register.
      · the returns are not additive. Five $1000 accounts is $5000 of capital.

    data : {symbol → OHLCV DataFrame}, each carrying REGIME_WARMUP_BARS of prefix
           BEFORE the trading window (see run_symbol's M1 note).
    """
    tokens = [t for t in tokens if t in data and len(data[t]) > trade_start_idx]
    if not tokens:
        raise ValueError("run_portfolio: no usable token data supplied")
    if SHORT_ENABLED:
        print("  ⚠️  SHORT_ENABLED=1 is ignored in portfolio mode — the live bot "
              "has no short sleeve, so including one would not be parity.",
              flush=True)

    # ── Per-token prep ────────────────────────────────────────────────────────
    if btc_df is not None:
        btc_df = precompute_indicators(btc_df)
    btc_ts_arr = (btc_df["ts"].to_numpy() if btc_df is not None
                  else np.empty(0, dtype=np.int64))

    frames: dict[str, pd.DataFrame] = {}
    regs:   dict[str, np.ndarray]   = {}
    ts_idx: dict[str, np.ndarray]   = {}
    hcache: dict[str, dict]         = {}
    for sym in tokens:
        d           = precompute_indicators(data[sym])
        frames[sym] = d
        regs[sym]   = regime.backtest_regimes(d, btc_df)
        ts_idx[sym] = d["ts"].to_numpy()
        hcache[sym] = {}

    # Master clock: every bar any token has, from the first tradable one onward.
    ref      = frames[tokens[0]]
    start_ts = int(ref["ts"].iloc[min(trade_start_idx, len(ref) - 1)])
    all_ts   = sorted({int(t) for sym in tokens for t in frames[sym]["ts"]
                       if int(t) >= start_ts})

    sym_states = {s: SymbolState(symbol=s) for s in tokens}
    mr_states  = {s: MRState(symbol=s)     for s in tokens}

    bal            = balance
    gates = _RiskGates(peak=balance, daily_start=balance)   # daily_sl_count is
    #                                    GLOBAL here, as the live bot keeps it

    legs: list[dict]  = []
    tally             = new_tally()
    equity_curve      = [balance]
    regime_counts     = {"BULL": 0, "NEUTRAL": 0, "BEAR": 0}
    bar_count         = 0
    halted            = False
    bankrupt          = False
    last_ts           = 0
    coverage: dict[str, list] = {}   # sym → [first_ts, last_ts, bars_seen]

    t0    = time.time()
    n_bar = len(all_ts)
    print(f"  ▶ replaying {n_bar:,} bars × {len(tokens)} symbols…", flush=True)

    for ts in all_ts:
        bar_count += 1
        last_ts    = ts
        if bar_count % 2000 == 0:
            print_progress(bar_count, n_bar, t0, bal,
                      f"  pos={len(legs)} legs")
        dt   = _bar_close_dt(ts)
        day  = dt.strftime("%Y-%m-%d")
        hour = dt.hour

        # ── Account-level gates, in the live bot's order (see _RiskGates) ──
        gate = gates.step(bal, day, hour)
        if gate.halt:
            halted   = True
            bankrupt = gate.halt == "bankrupt"
            break
        size_factor = gate.size_factor
        block_new   = gate.block_new
        btc_row     = _row_for_ts(btc_ts_arr, ts)

        # Token order matters and matches live: a symbol that opens a full
        # position early in the loop occupies a MAX_OPEN slot for the ones after it
        # on the SAME bar (paper_bb iterates self.tokens in config order).
        for sym in tokens:
            i = _row_for_ts(ts_idx[sym], ts)
            if i is None or i < 65:
                continue          # bar missing / not warm — live skips the symbol
            c = coverage.get(sym)
            if c is None:
                coverage[sym] = [ts, ts, 1]
            else:
                c[1], c[2] = ts, c[2] + 1
            d   = frames[sym]
            row = d.iloc[i]
            reg = str(regs[sym][i])
            s   = sym_states[sym]
            if s.state == IDLE:
                regime_counts[reg] = regime_counts.get(reg, 0) + 1

            snap = build_snapshot(
                d, i, sym, btc_df=btc_df,
                hurst_override=_hurst_at(d["close"], i, hcache[sym]),
                btc_row=btc_row)

            open_full = sum(1 for st in sym_states.values()
                            if st.state in (SCALE_OPEN, TRAILING))

            mom_events, mr_events, info = process_symbol_bar(
                s, mr_states[sym], snap, float(row["high"]), float(row["low"]),
                reg=reg, size_factor=size_factor, block_new=block_new,
                open_full=open_full,
                rsi_val=snap["indicators"]["rsi_14"],
                vol_ratio=snap["volume"]["volume_ratio"])
            if info["block_new_full"]:
                tally["maxopen_block"] += 1

            sl_before       = tally["SL"]
            bal            += account_events(mom_events, mr_events, legs, tally, ts)
            gates.count_stop_loss(tally["SL"] - sl_before)

        equity_curve.append(bal)

    if sys.stdout.isatty():
        print(flush=True)          # close the progress line

    # ── Metrics ───────────────────────────────────────────────────────────────
    positions = aggregate_positions(legs)
    pos_stats = position_stats(positions, risk_per_trade=RISK_PER_TRADE_USD,
                               risk_by_sleeve=RISK_BY_SLEEVE)
    entry_fees = sum(leg["pnl"] for leg in legs if leg["exit_type"] == "OPEN")

    recon_err = (bal - balance) - sum(leg["pnl"] for leg in legs)
    if abs(recon_err) > 0.01:
        print(f"  ⚠️  leg log does not reconcile with the equity curve "
              f"(off by ${recon_err:+.4f}) — a balance-moving leg is missing.",
              flush=True)

    eq    = np.array(equity_curve)
    pk    = np.maximum.accumulate(eq)
    maxdd = float(((eq - pk) / pk).min()) * 100
    total_return = (bal - balance) / balance * 100

    first_ts = all_ts[0] if all_ts else 0
    days_run = ((last_ts - first_ts) / 86_400_000) if bar_count > 1 else 0.0
    days_eff = days_run if days_run > 0.5 else float(days)

    return {
        "symbols":       tokens,
        "days":          days,
        "bars":          bar_count,
        "halted":        halted,
        "start_ts":      first_ts,
        "end_ts":        last_ts,          # where the replay ACTUALLY stopped
        "window_end_ts": all_ts[-1] if all_ts else 0,
        "days_run":      days_run,
        "n_halts":       gates.n_halts,
        "bankrupt":      bankrupt,
        "pos_stats":     pos_stats,
        "pos_exits":     exit_distribution(positions),
        "_positions":    positions,
        "_legs":         legs,
        "recon_err":     recon_err,
        "entry_fees":    entry_fees,
        "start_balance": balance,
        "final_balance": bal,
        "total_return":  total_return,
        "monthly_est":   total_return / days_eff * 30,
        "max_dd":        maxdd,
        "regime_counts": regime_counts,
        "tally":         tally,
        "coverage":      {k: {"first": v[0], "last": v[1], "bars": v[2]}
                          for k, v in coverage.items()},
    }




# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BreakoutBot Backtest — default mode mirrors the live bot")
    parser.add_argument("--tokens", nargs="+", default=None,
                        help="Tokens to test (default: config.TOKENS, the live universe)")
    parser.add_argument("--days",    type=int,   default=90)
    parser.add_argument("--balance", type=float, default=INITIAL_BALANCE,
                        help="Starting equity. In portfolio mode this is the WHOLE "
                             "account, as it is live; in --per-symbol mode each "
                             "token gets this much on its own.")
    parser.add_argument("--per-symbol", action="store_true",
                        help="Legacy mode: one independent account per token and no "
                             "MAX_OPEN cap. Good for RANKING coins, not a picture "
                             "of the deployed account.")
    parser.add_argument("--all", action="store_true",
                        help="Run the full 23-coin universe instead of the live 5")
    parser.add_argument("--beta", action="store_true",
                        help="Print beta ranking vs BTC after the backtest")
    parser.add_argument("--no-btc-gate", action="store_true",
                        help="Drop BTC from the regime blend (ablation)")
    parser.add_argument("--restarts", action="store_true",
                        help="Continue past a PEAK_DD_LIMIT hard stop by rebasing "
                             "the equity peak — models an operator restarting the "
                             "bot each time. Needed for any multi-year study, but "
                             "NOT live parity (same as BT_RESTARTS=1).")
    parser.add_argument("--cache", action="store_true",
                        help="Reuse/store the window under backtests/data/ (shared "
                             "with bench.py). Off by default, so a plain run always "
                             "means 'the last N days as of now'.")
    args = parser.parse_args()

    if args.restarts:
        globals()["_RESTARTS"] = True

    check_live_params()

    tokens = list(_FULL_UNIVERSE) if args.all else (args.tokens or list(TOKENS))
    need   = list(dict.fromkeys(tokens + ["BTCUSDT"]))   # BTC always: regime blend

    print(f"📡 Window: last {args.days}d + {REGIME_WARMUP_DAYS}d regime warm-up "
          f"| {len(need)} symbols | {'portfolio (live parity)' if not args.per_symbol else 'per-symbol'} mode")
    frames = load_frames(need, args.days, use_cache=args.cache)

    btc_df: pd.DataFrame | None = None
    if BTC_GATE_ENABLED and not args.no_btc_gate:
        btc_df = frames["BTCUSDT"]
    else:
        print("ℹ️  BTC dropped from the regime blend (ablation run).")

    _span = frames[tokens[0]]
    _a = datetime.fromtimestamp(int(_span["ts"].iloc[0])  / 1000, tz=timezone.utc)
    _b = datetime.fromtimestamp(int(_span["ts"].iloc[-1]) / 1000, tz=timezone.utc)
    print(f"   data {_a:%Y-%m-%d} → {_b:%Y-%m-%d}  ({len(_span):,} bars; the first "
          f"{REGIME_WARMUP_BARS:,} are warm-up and are never traded)")

    if args.per_symbol:
        all_results: list[dict] = []
        for tok in tokens:
            try:
                r = run_symbol(tok, args.days, args.balance,
                               btc_df=btc_df.copy() if btc_df is not None else None,
                               df=frames[tok].copy(),
                               trade_start_idx=REGIME_WARMUP_BARS)
                print_report(r)
                r.pop("_df", None)
                all_results.append(r)
            except Exception as e:
                print(f"\n  ❌ {tok} failed: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc()

        if len(all_results) > 1:
            n_ok    = sum(1 for r in all_results if r["total_return"] > 0)
            monthly = sum(r["monthly_est"] * args.balance / 100 for r in all_results)
            tpd     = sum(r["trades_per_day"] for r in all_results)
            avg_wr  = sum(r["win_rate"] for r in all_results) / len(all_results)
            pooled  = position_stats(
                [p for r in all_results for p in r.get("_positions", [])],
                risk_per_trade=RISK_PER_TRADE_USD, risk_by_sleeve=RISK_BY_SLEEVE)
            print(f"\n{'═'*56}")
            print(f"  COMBINED ({len(all_results)} independent accounts, {n_ok} profitable)")
            print(f"  Total trades/day : {tpd:.1f}")
            print(f"  Avg Win Rate     : {avg_wr:.1f}%  (record-based, inflated)")
            print(f"  Monthly est ($)  : ${monthly:+.2f}  "
                  f"(on ${args.balance:.0f} EACH — implies ${args.balance*len(all_results):.0f} deployed)")
            print(f"  Pooled positions : {pooled['n_positions']}  WR {pooled['win_rate']}%  "
                  f"exp ${pooled['expectancy']:+.2f}"
                  + (f" ({pooled['expectancy_r']:+.3f} R)"
                     if pooled['expectancy_r'] is not None else ""))
            print(f"  By sleeve        : {sleeve_line(pooled)}")
            print(f"{'═'*56}")
            print("  ⚠️  These are SEPARATE accounts with no MAX_OPEN cap and no "
                  "shared drawdown guard.\n      Drop --per-symbol for the "
                  "live-parity portfolio run.")
    else:
        r = run_portfolio(tokens, args.days, args.balance,
                          data={t: frames[t].copy() for t in tokens},
                          btc_df=btc_df.copy() if btc_df is not None else None,
                          trade_start_idx=REGIME_WARMUP_BARS)
        print_portfolio_report(r)
        # BT_RUN_TAG keeps parallel arms from overwriting each other's dump: two
        # arms on the SAME window write the same path otherwise, and the second
        # one silently wins. Unset → the old filename, so nothing else changes.
        _tag = os.getenv("BT_RUN_TAG", "")
        dump_run(r, f"{CACHE_DIR}/last_run_{args.days}d{'_' + _tag if _tag else ''}.json")

    # ── Optional beta ranking (reuses fetched data — no extra network calls) ──
    if args.beta and btc_df is not None:
        print_beta_ranking(tokens, args.days, btc_df, prefetched=frames)