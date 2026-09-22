"""
BreakoutBot — Live Paper Trading Engine

Runs the Test-Confirm-Scale strategy live on all configured TOKENS.
Two modes:

  python paper_bb.py               # pure local simulation (no API key needed)
  python paper_bb.py --testnet     # place real orders on Binance Futures Testnet
                                   # (requires secrets_local.py with API credentials)

Usage:
    python paper_bb.py                           # simulate locally
    python paper_bb.py --testnet                 # testnet live orders
    python paper_bb.py --tokens SOLUSDT INJUSDT  # subset of tokens
    python paper_bb.py --resume                  # resume from saved state
    python paper_bb.py --status                  # print status and exit

Strategy: Wave 11 MathEngine signal → $20 test → 1-bar confirm → $300×3x full → TP1/TP2/TRAIL

Exit with Ctrl-C; state is saved automatically to state_paper.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, NamedTuple

import regime
from config import (
    TOKENS, INITIAL_BALANCE, MR_ENABLED,
    DAILY_DD_LIMIT, EQUITY_THROTTLE_DD, PEAK_DD_LIMIT, DAILY_SL_LIMIT,
    SESSION_START_UTC, SESSION_END_UTC,
    TEST_SIZE_USD, MAX_OPEN, RISK_PER_TRADE_USD,
)
from indicators import build_snapshot, hurst_exponent, precompute_indicators
from strategy import SymbolState, IDLE, SCALE_OPEN, TRAILING, TEST_OPEN
from mean_reversion import MRState
from metrics import aggregate_positions, position_stats
# Market data and the retired testnet executor used to live in this file. They
# are imported BY NAME so a test or replay harness can still patch
# `paper_bb.fetch_recent` exactly the way it always could.
from market_data import FETCH_BARS, fetch_4h, fetch_recent, wait_for_bar_close
from testnet_orders import TestnetOrderManager

if TYPE_CHECKING:
    import pandas as pd

STATE_FILE = "state_paper.json"
LOG_FILE   = "paper_bb.log"

BARS_WARM  = 80    # skip first 80 bars (indicator warmup)


class _Reading(NamedTuple):
    """One symbol's view of one closed bar, as the sleeves consume it."""
    snap:      dict
    high:      float
    low:       float
    rsi:       float
    vol_ratio: float


def _new_funnel() -> dict[str, int]:
    """Per-bar signal-funnel counters (see _accumulate_funnel)."""
    return {"scanned": 0, "regime_pass": 0, "probe": 0, "confirm_ok": 0,
            "confirm_fail": 0, "full": 0, "blocked_max_open": 0}


# ── Paper trader engine ───────────────────────────────────────────────────────

class PaperTrader:
    """Stateful paper trading engine: polls Binance every 5 min, processes bars."""

    def __init__(self, tokens: list[str], resume: bool = False,
                 testnet_om: "TestnetOrderManager | None" = None):
        self.tokens     = tokens
        self.testnet_om = testnet_om   # None → pure simulation; set → real testnet orders

        # Portfolio-level tracking
        self.balance       = INITIAL_BALANCE
        self.peak          = INITIAL_BALANCE
        self.daily_start   = INITIAL_BALANCE
        self.daily_day     = ""
        self.daily_freeze  = False
        self.size_factor   = 1.0   # 1.0 normal · 0.5 when in -7% throttle zone
        self.daily_sl_count= 0
        self.bar_count     = 0

        # Per-symbol state machines
        self.sym_states: dict[str, SymbolState] = {
            tok: SymbolState(symbol=tok) for tok in tokens
        }
        # Symbols carrying a position from a universe they are no longer part of
        # (E16). Managed to the exit, never re-entered. Populated by _load_state.
        self.winddown: set[str] = set()
        # FAZ 2: range mean-reversion sleeve (parallel to momentum)
        self.mr_states: dict[str, MRState] = {
            tok: MRState(symbol=tok) for tok in tokens
        }
        self.run_mr_tp = self.run_mr_sl = self.run_mr_tmo = 0
        # 4h regime cache (refreshed when a new 4h bar closes — slow timescale)
        self._regime: dict[str, str] = {tok: "NEUTRAL" for tok in tokens}
        self._regime_4h_ts = 0   # ms of last 4h-bar boundary we refreshed on

        # Trade log (dicts, serialisable)
        self.trade_log: list[dict] = []

        # Stats accumulators (this run only — reset on resume)
        self.run_tp1 = self.run_tp2 = self.run_sl = 0
        self.run_trail = self.run_tmo = 0
        self.run_confirm_ok = self.run_confirm_fail = 0
        # Cumulative drag from probes that never became positions (CONFIRM_FAIL +
        # TEST SL). Tracked separately because it is invisible in the trade log
        # yet accounted for ~54% of the live drawdown.
        self.probe_cost = 0.0
        # T0: ISO timestamp from which trade_log carries EVERY balance-moving
        # leg (OPEN + probe legs included). None until the first save.
        self.full_leg_logging_since: str | None = None
        # Signal-funnel totals across the run — compare against the backtest's
        # confirm rate to detect live/backtest divergence.
        self.funnel_totals = {"scanned": 0, "regime_pass": 0, "probe": 0,
                              "confirm_ok": 0, "confirm_fail": 0, "full": 0,
                              "blocked_max_open": 0}

        # Log file (line-buffered so tail -f works)
        self.log_f = open(LOG_FILE, "a", buffering=1)

        if resume and os.path.exists(STATE_FILE):
            self._load_state()

    # ── State persistence ─────────────────────────────────────────────────────

    def _save_state(self) -> None:
        if self.full_leg_logging_since is None:
            # First save under the full-leg schema — everything from here on
            # reconciles; everything before it does not (T0).
            self.full_leg_logging_since = datetime.now(timezone.utc).isoformat()
        sym_data = {}
        for tok, s in self.sym_states.items():
            sym_data[tok] = {
                "state":         s.state,
                "direction":     s.direction,
                "cooldown":      s.cooldown,
                "test_entry":    s.test_entry,
                "test_sl":       s.test_sl,
                "test_atr":      s.test_atr,
                "full_entry":    s.full_entry,
                "full_sl":       s.full_sl,
                "full_tp1":      s.full_tp1,
                "full_tp2":      s.full_tp2,
                "full_notional": s.full_notional,
                "tp1_hit":       s.tp1_hit,
                "be_price":      s.be_price,
                "trail_best":    s.trail_best,
                "bars_held":     s.bars_held,
            }
        state = {
            "balance":        self.balance,
            "peak":           self.peak,
            "daily_start":    self.daily_start,
            "daily_day":      self.daily_day,
            "daily_freeze":   self.daily_freeze,
            "daily_sl_count": self.daily_sl_count,
            "bar_count":      self.bar_count,
            "trade_log":      self.trade_log,
            "sym_states":     sym_data,
            "mr_states":      {t: {"state": m.state, "entry": m.entry,
                                   "notional": m.notional, "sl": m.sl, "tp": m.tp,
                                   "bars_in": m.bars_in, "cooldown": m.cooldown}
                               for t, m in self.mr_states.items()},
            "regime":         self._regime,
            "regime_4h_ts":   self._regime_4h_ts,
            "funnel_totals":  self.funnel_totals,
            "probe_cost":     round(self.probe_cost, 4),
            # T0 cutover: from this bar on, trade_log contains EVERY leg that
            # moved the balance, so sum(pnl) reconciles exactly. Legs written
            # before it are missing their OPEN-leg fees and cannot be repaired
            # per-trade -- any reconciliation assert must be scoped to trades at
            # or after this marker.
            "full_leg_logging_since": self.full_leg_logging_since,
            "schema_version":         2,
        }
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    def _load_state(self) -> None:
        try:
            with open(STATE_FILE) as f:
                st = json.load(f)
            self.balance        = st["balance"]
            self.peak           = st["peak"]
            self.daily_start    = st["daily_start"]
            self.daily_day      = st["daily_day"]
            self.daily_freeze   = st["daily_freeze"]
            self.daily_sl_count = st["daily_sl_count"]
            self.bar_count      = st["bar_count"]
            self.trade_log      = st.get("trade_log", [])
            self.probe_cost     = st.get("probe_cost", 0.0)
            self.full_leg_logging_since = st.get("full_leg_logging_since")
            self.funnel_totals.update(st.get("funnel_totals", {}))
            # E16 — a coin dropped from TOKENS while it still held a position used
            # to be skipped here, and the position simply ceased to exist: no
            # closing leg in trade_log, its unrealized PnL never booked, and on a
            # --testnet bot the exchange position left open with nothing managing
            # it. This already happened during the 8→5 shrink on 2026-08-22, and
            # every re-curation is another chance for it.
            #
            # Orphans are adopted into a WIND-DOWN set instead: they keep being
            # processed each bar so their stops and targets still fire, but they
            # are barred from opening anything new, and they leave the book for
            # good once they go flat.
            for tok, saved in st.get("sym_states", {}).items():
                if tok not in self.sym_states:
                    if str(saved.get("state", IDLE)) != IDLE:
                        self.sym_states[tok] = SymbolState(symbol=tok)
                        self.winddown.add(tok)
                        self._log(
                            f"⚠️  {tok} açık pozisyonla evrenden çıkmış "
                            f"({saved.get('state')}) — WIND-DOWN: yeni giriş yok, "
                            f"mevcut pozisyon kapanana kadar yönetilecek."
                        )
                    else:
                        continue
                s = self.sym_states[tok]
                s.state         = saved["state"]
                s.direction     = saved["direction"]
                s.cooldown      = saved["cooldown"]
                s.test_entry    = saved["test_entry"]
                s.test_sl       = saved["test_sl"]
                s.test_atr      = saved["test_atr"]
                s.full_entry    = saved["full_entry"]
                s.full_sl       = saved["full_sl"]
                s.full_tp1      = saved["full_tp1"]
                s.full_tp2      = saved["full_tp2"]
                s.full_notional = saved["full_notional"]
                s.tp1_hit       = saved["tp1_hit"]
                s.be_price      = saved["be_price"]
                s.trail_best    = saved["trail_best"]
                s.bars_held     = saved["bars_held"]
            for tok, m in st.get("mr_states", {}).items():
                if tok in self.mr_states:
                    s2 = self.mr_states[tok]
                    s2.state, s2.entry, s2.notional = m["state"], m["entry"], m["notional"]
                    s2.sl, s2.tp = m["sl"], m["tp"]
                    s2.bars_in, s2.cooldown = m["bars_in"], m["cooldown"]
            # Keep only the CURRENT universe: a coin dropped from TOKENS must not
            # linger here. Stale keys are never used for trading (regimes are read
            # per-symbol while iterating self.tokens) but they are persisted back to
            # state, they skew the "BULL n/N" line, and the track-record exporter
            # derives the published universe — and the own-universe HODL benchmark —
            # from these very keys.
            saved_regime = st.get("regime", {})
            self._regime = {t: saved_regime.get(t, "NEUTRAL") for t in self.tokens}
            self._regime_4h_ts = st.get("regime_4h_ts", 0)
            self._log(
                f"✅ Resumed from {STATE_FILE} — "
                f"bar #{self.bar_count}, balance ${self.balance:.2f}"
            )
        except Exception as exc:
            self._log(f"⚠️  Could not load state ({exc}) — starting fresh")

    # ── Trade log ─────────────────────────────────────────────────────────────

    def _log_leg(self, ev, bar_dt, sleeve: str, exit_type: str | None = None) -> None:
        """Append ONE leg to trade_log.

        T0 — why every leg goes in, including the ones that open a position:
            Each OPEN leg carries pnl = -notional x EXEC_COST_PER_SIDE (the entry
            fee) and is applied to self.balance. It used to `continue` before the
            append below, so the fee left the balance without ever appearing in
            the trade log. Same for probe legs (CONFIRM_OK / CONFIRM_FAIL /
            test-SL). The result was that sum(trade_log.pnl) did NOT equal the
            balance change -- measured -$53.41 on the 8-coin bot and -$33.35 on
            the 5-coin one -- and the published expectancy had the wrong sign.

            aggregate_positions() ignores exit_type "OPEN", so logging these does
            not change the position count; it only makes the money add up. Probe
            legs land in their own PROBE sleeve for the same reason.
        """
        self.trade_log.append({
            "ts":        bar_dt.isoformat(),
            "symbol":    ev.symbol,
            "direction": ev.direction,
            "sleeve":    sleeve,
            "kind":      getattr(ev, "kind", ""),
            "exit_type": exit_type or ev.exit_type,
            "entry":     ev.entry,
            "exit":      ev.exit,
            "pnl":       round(ev.pnl, 4),
            "balance":   round(self.balance, 4),
        })

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log(self, msg: str, also_print: bool = True) -> None:
        ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        line = f"[{ts}] {msg}"
        if also_print:
            print(line, flush=True)
        self.log_f.write(line + "\n")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _blocks_new_entries(self, symbol: str, in_session: bool) -> bool:
        """Whether `symbol` is barred from opening anything on this bar.

        Three independent reasons, and a wind-down symbol (E16) is the third:
        it is kept on the book only to manage the position it already holds, so
        it must never be able to re-enter — otherwise dropping a coin from
        TOKENS would have no effect at all while it happened to be in a trade.
        """
        return self.daily_freeze or not in_session or symbol in self.winddown

    def _open_full_count(self) -> int:
        """Count symbols currently in SCALE_OPEN or TRAILING (a full position is open)."""
        return sum(
            1 for s in self.sym_states.values()
            if s.state in (SCALE_OPEN, TRAILING)
        )

    def _print_status(self) -> None:
        """Print a concise status block every N bars."""
        ret  = (self.balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100
        pk_dd= (self.balance - self.peak) / self.peak * 100 if self.peak > 0 else 0.0
        day_dd=(self.balance - self.daily_start) / self.daily_start * 100 \
               if self.daily_start > 0 else 0.0

        closed = [t for t in self.trade_log if t.get("exit_type") not in ("OPEN",)]
        # Position-based: TP1 logs its own record and the post-TP1 leg cannot
        # lose, so counting records inflates WR by ~14 points. See metrics.py.
        pstats = position_stats(aggregate_positions(closed),
                                risk_per_trade=RISK_PER_TRADE_USD)

        open_list = [
            f"  {tok} {s.state}({s.direction}) bars={s.bars_held}"
            for tok, s in self.sym_states.items()
            if s.state != IDLE
        ]

        print("─" * 60, flush=True)
        print(f"  Bar #{self.bar_count:,} | Balance: ${self.balance:.2f} | Return: {ret:+.2f}%", flush=True)
        print(f"  PeakDD: {pk_dd:.1f}%  DayDD: {day_dd:.1f}%  DayFreeze: {self.daily_freeze}", flush=True)
        print(f"  Positions: {pstats['n_positions']}  WR: {pstats['win_rate']:.1f}% "
              f"(BE {pstats['breakeven_wr']}%)  payoff {pstats['payoff']}  "
              f"exp ${pstats['expectancy']:+.2f}  Open: {self._open_full_count()}/{MAX_OPEN}",
              flush=True)
        ft = self.funnel_totals
        _cr = (100 * ft["confirm_ok"] / (ft["confirm_ok"] + ft["confirm_fail"])
               if (ft["confirm_ok"] + ft["confirm_fail"]) else 0.0)
        print(f"  Funnel: probe={ft['probe']} confirm={_cr:.0f}% full={ft['full']} "
              f"maxopen_block={ft['blocked_max_open']} | probe drag: ${self.probe_cost:+.2f}",
              flush=True)
        if open_list:
            print("  Positions:", flush=True)
            for line in open_list:
                print(line, flush=True)
        print("─" * 60, flush=True)

    # ── Regime refresh (4h, slow timescale) ───────────────────────────────────

    def _refresh_regimes(self) -> None:
        """Recompute per-coin 4h regime when a new 4h bar has closed."""
        span  = 4 * 3600 * 1000
        cur_b = (int(time.time() * 1000) // span) * span
        if cur_b == self._regime_4h_ts:
            return                                  # already current for this 4h bar
        try:
            btc_4h = fetch_4h("BTCUSDT")
        except Exception as exc:
            self._log(f"⚠️  BTC 4h fetch failed ({exc}) — keeping previous regimes")
            return
        for tok in self.tokens:
            try:
                self._regime[tok] = regime.current_regime(fetch_4h(tok), btc_4h)
            except Exception as exc:
                self._log(f"  ⚠️  {tok} 4h regime failed ({exc}) — keep prev",
                          also_print=False)
        self._regime_4h_ts = cur_b
        bull = [t for t, r in self._regime.items() if r == "BULL"]
        # Describe the sleeves that are actually running: MR was switched off on
        # 2026-08-27 and a log line still promising "MR active in NEUTRAL" sends
        # the next reader hunting for trades that cannot happen.
        others = ("MR active in NEUTRAL" if MR_ENABLED
                  else "MR off — nothing trades outside BULL")
        self._log(f"🧭 Regime refresh — BULL: {bull or '—'} "
                  f"(others throttle longs→0; {others})")

    # ── Bar processing ────────────────────────────────────────────────────────
    #
    # _process_bar is the reference implementation of the gate ORDER that
    # backtest.py mirrors (see its PARITY CONTRACT). The order below is that
    # contract, one named step per gate:
    #
    #   1. _roll_daily_window     — new UTC day resets the daily budget
    #   2. _guard_peak_drawdown   — hard stop, exits the process
    #   3. _apply_equity_throttle — halve sizing in a -7% drawdown
    #   4. _apply_daily_freeze    — -5% intraday bars new entries
    #   5. per symbol: _symbol_snapshot → _run_momentum_sleeve → _run_mr_sleeve
    #   6. _log_bar_summary / _save_state
    #
    # Splitting a gate out of this method without mirroring it in backtest.py
    # turns every backtest number into a fiction.

    def _process_bar(self, bar_dt: datetime) -> None:
        self.bar_count += 1

        self._roll_daily_window(bar_dt.strftime("%Y-%m-%d"))
        peak_dd = self._guard_peak_drawdown()
        self._apply_equity_throttle(peak_dd)
        self._apply_daily_freeze()

        in_session = SESSION_START_UTC <= bar_dt.hour < SESSION_END_UTC
        btc_df = self._fetch_btc_reference()

        self._refresh_regimes()
        n_bull = sum(1 for r in self._regime.values() if r == "BULL")
        gate_str = f"regime: BULL {n_bull}/{len(self.tokens)}"

        funnel = _new_funnel()

        # Wind-down symbols (E16) ride along until flat so their exits still fire.
        for symbol in list(self.tokens) + sorted(self.winddown):
            s = self.sym_states[symbol]
            if self._release_if_wound_down(symbol, s):
                continue

            reading = self._symbol_snapshot(symbol, btc_df)
            if reading is None:
                continue

            reg       = self._regime.get(symbol, "NEUTRAL")
            size_mult = regime.LONG_SIZE_MULT.get(reg, 0.0) * self.size_factor
            block_new = self._blocks_new_entries(symbol, in_session)

            self._run_momentum_sleeve(symbol, s, reading, bar_dt,
                                      size_mult, block_new, funnel)
            self._run_mr_sleeve(symbol, s, reading, bar_dt, reg, block_new)

        self._log_bar_summary(bar_dt, gate_str)
        self._accumulate_funnel(funnel)
        self._save_state()

        # Detailed status every 12 bars (≈ 1 hour)
        if self.bar_count % 12 == 0:
            self._print_status()

    # ── Bar processing · account-level gates ──────────────────────────────────

    def _roll_daily_window(self, day: str) -> None:
        """Reset the daily risk budget when the UTC day turns over."""
        if day == self.daily_day:
            return
        self.daily_day      = day
        self.daily_start    = self.balance
        self.daily_freeze   = False
        self.daily_sl_count = 0
        self._log(f"📅 New day {day} | starting balance ${self.balance:.2f}")

    def _guard_peak_drawdown(self) -> float:
        """Update the equity peak and hard-stop the process if the guard breaks.

        Live, this is where a human decides whether to restart — hence sys.exit
        rather than a flag. backtest.py models the same decision with BT_RESTARTS.
        """
        if self.balance > self.peak:
            self.peak = self.balance
        peak_dd = (self.balance - self.peak) / self.peak if self.peak > 0 else 0.0
        if peak_dd <= PEAK_DD_LIMIT:
            self._log(
                f"🛑 PEAK DD {peak_dd:.1%} hit PEAK_DD_LIMIT ({PEAK_DD_LIMIT:.0%}) "
                f"— hard stop. Saving state."
            )
            self._save_state()
            self._print_status()
            sys.exit(1)
        return peak_dd

    def _apply_equity_throttle(self, peak_dd: float) -> None:
        """Halve position sizing while the account sits in a -7% drawdown."""
        new_factor = 0.5 if peak_dd <= EQUITY_THROTTLE_DD else 1.0
        if new_factor == self.size_factor:
            return
        self.size_factor = new_factor
        if new_factor < 1.0:
            self._log(f"🔻 THROTTLE on: peak DD {peak_dd:.1%} ≤ "
                      f"{EQUITY_THROTTLE_DD:.0%} — position size ×0.5")
        else:
            self._log(f"🔺 THROTTLE off: peak DD {peak_dd:.1%} recovered — full size")

    def _apply_daily_freeze(self) -> None:
        """Bar new entries for the rest of the day after a -5% intraday loss."""
        intraday_dd = (self.balance - self.daily_start) / self.daily_start \
                      if self.daily_start > 0 else 0.0
        if intraday_dd <= DAILY_DD_LIMIT and not self.daily_freeze:
            self.daily_freeze = True
            self._log(f"⚠️  Daily DD {intraday_dd:.1%} hit — freezing new entries today")

    # ── Bar processing · market reading ───────────────────────────────────────

    def _fetch_btc_reference(self) -> "pd.DataFrame | None":
        """BTC 5m frame used for beta. A failed fetch degrades beta, not the bar."""
        try:
            return precompute_indicators(fetch_recent("BTCUSDT", FETCH_BARS))
        except Exception as exc:
            self._log(f"⚠️  BTC 5m fetch failed ({exc})")
            return None

    def _release_if_wound_down(self, symbol: str, s: SymbolState) -> bool:
        """Drop a wind-down symbol (E16) from the book once it reaches flat."""
        if symbol not in self.winddown or s.state != IDLE:
            return False
        self.winddown.discard(symbol)
        self.sym_states.pop(symbol, None)
        self.mr_states.pop(symbol, None)
        self._log(f"✅ {symbol} wind-down tamamlandı — kitaptan çıkarıldı.")
        return True

    def _symbol_snapshot(self, symbol: str,
                         btc_df: "pd.DataFrame | None") -> "_Reading | None":
        """Fetch this symbol's frame and build the snapshot the sleeves read.

        None means the bar is skipped for this symbol — a fetch error or a frame
        too short to have warmed the indicators. The backtest skips a symbol with
        no row for the bar for the same reason.
        """
        try:
            df = precompute_indicators(fetch_recent(symbol, FETCH_BARS))
        except Exception as exc:
            self._log(f"  ⚠️  {symbol} fetch failed: {exc}")
            return None

        if len(df) < BARS_WARM:
            return None                      # not enough history yet

        idx = len(df) - 1
        high = float(df["high"].iloc[idx])
        low  = float(df["low"].iloc[idx])

        # Hurst on last 40 bars (regime indicator)
        hurst_val = hurst_exponent(df["close"].iloc[max(0, idx - 39): idx + 1])

        # BTC row alignment (find matching timestamp for beta calc)
        btc_row = None
        if btc_df is not None:
            ts_now   = int(df["ts"].iloc[idx])
            btc_idxs = btc_df.index[btc_df["ts"] == ts_now]
            btc_row  = int(btc_idxs[0]) if len(btc_idxs) > 0 else None

        snap = build_snapshot(df, idx, symbol,
                              btc_df=btc_df,
                              hurst_override=hurst_val,
                              btc_row=btc_row)
        return _Reading(snap=snap, high=high, low=low,
                        rsi=snap["indicators"]["rsi_14"],
                        vol_ratio=snap["volume"]["volume_ratio"])

    # ── Bar processing · momentum sleeve ──────────────────────────────────────

    def _run_momentum_sleeve(self, symbol: str, s: SymbolState, reading: "_Reading",
                             bar_dt: datetime, size_mult: float, block_new: bool,
                             funnel: dict) -> None:
        """Step the Test-Confirm-Scale machine and book whatever it returns."""
        funnel["scanned"] += 1
        if size_mult > 0 and not block_new:
            funnel["regime_pass"] += 1

        events = []
        if not (s.state == IDLE and (block_new or size_mult <= 0)):
            open_full      = self._open_full_count()
            block_new_full = (s.state == TEST_OPEN and open_full >= MAX_OPEN)
            if block_new_full:
                funnel["blocked_max_open"] += 1
            events = s.process_bar(reading.snap, reading.high, reading.low,
                                   reading.rsi, reading.vol_ratio,
                                   block_new_full=block_new_full,
                                   size_mult=size_mult)

        for ev in events:
            self.balance += ev.pnl
            if ev.exit_type == "OPEN":
                self._book_open(ev, s, bar_dt, funnel)
            elif ev.kind == "TEST":
                self._book_probe_result(ev, bar_dt, funnel)
            else:
                self._book_full_close(ev, symbol, bar_dt)

    def _book_open(self, ev, s: SymbolState, bar_dt: datetime, funnel: dict) -> None:
        """A probe or a full position just opened. The entry fee is already paid."""
        tn = self.testnet_om
        if ev.kind == "TEST":
            funnel["probe"] += 1
            self._log(
                f"  🔬 TEST OPEN  {ev.symbol} {ev.direction} "
                f"@ {ev.entry:.5g} | bal=${self.balance:.2f}"
            )
            if tn:
                fill = tn.open_market(ev.symbol, ev.direction,
                                      TEST_SIZE_USD, ev.entry)
                self._log(f"     ↳ [TESTNET] TEST order filled @ {fill:.5g}",
                          also_print=False)
        else:
            funnel["full"] += 1
            funnel["confirm_ok"] += 1
            # FIX #1: use the strategy's dynamic, regime+throttle-aware notional
            # (risk-based sizing) — NOT a flat FULL_SIZE_USD×LEVERAGE.
            notional = s.full_notional
            self._log(
                f"  📈 FULL OPEN  {ev.symbol} {ev.direction} "
                f"@ {ev.entry:.5g} | notional=${notional:.0f} "
                f"| bal=${self.balance:.2f}"
            )
            if tn:
                fill = tn.open_market(ev.symbol, ev.direction, notional, ev.entry)
                self._log(f"     ↳ [TESTNET] FULL order filled @ {fill:.5g}",
                          also_print=False)
        # T0: the entry fee already hit self.balance — log the leg.
        self._log_leg(ev, bar_dt, "PROBE" if ev.kind == "TEST" else "MOMENTUM")

    def _book_probe_result(self, ev, bar_dt: datetime, funnel: dict) -> None:
        """A $20 probe resolved: confirmed, failed, or stopped out.

        A failed probe is money spent on a position that never existed. On the
        live record that drag was ~54% of the total loss, so it is tracked
        explicitly in probe_cost rather than inferred.
        """
        tn = self.testnet_om
        if ev.exit_type == "CONFIRM_OK":
            self.run_confirm_ok += 1
            self._log(
                f"  ✅ CONFIRMED  {ev.symbol} {ev.direction} "
                f"pnl=${ev.pnl:+.3f} | bal=${self.balance:.2f}"
            )
            if tn:
                fill = tn.close_market(ev.symbol, ev.direction, 1.0)
                self._log(f"     ↳ [TESTNET] TEST closed @ {fill:.5g}",
                          also_print=False)
        elif ev.exit_type == "CONFIRM_FAIL":
            self.run_confirm_fail += 1
            funnel["confirm_fail"] += 1
            self.probe_cost += ev.pnl
            self._log(
                f"  ❌ CONF FAIL  {ev.symbol} {ev.direction} "
                f"pnl=${ev.pnl:+.3f} | bal=${self.balance:.2f}"
            )
            if tn:
                fill = tn.close_market(ev.symbol, ev.direction, 1.0)
                self._log(f"     ↳ [TESTNET] TEST closed @ {fill:.5g}",
                          also_print=False)
        elif ev.exit_type == "SL":
            # Stopped out before it could be confirmed — same category of cost
            # as a CONFIRM_FAIL: paid for, never traded.
            self.probe_cost += ev.pnl
            self._log(
                f"  🛑 TEST SL    {ev.symbol} pnl=${ev.pnl:+.3f} "
                f"| bal=${self.balance:.2f}"
            )
        # T0: probe legs are real closed round-trips with real cost. A test-SL is
        # logged as PROBE_SL so the aggregator never reads it as a position SL.
        self._log_leg(ev, bar_dt, "PROBE",
                      exit_type="PROBE_SL" if ev.exit_type == "SL" else ev.exit_type)

    def _book_full_close(self, ev, symbol: str, bar_dt: datetime) -> None:
        """A full position closed at TP1/TP2/SL/TRAIL/TIMEOUT."""
        emoji = "✅" if ev.pnl > 0 else "❌"
        self._log(
            f"  {emoji} CLOSE FULL  {ev.symbol} [{ev.exit_type}] "
            f"{ev.direction} entry={ev.entry:.5g} exit={ev.exit:.5g} "
            f"pnl=${ev.pnl:+.2f} | bal=${self.balance:.2f}"
        )

        tn = self.testnet_om
        if tn:
            if ev.exit_type == "TP1":
                fill = tn.close_market(ev.symbol, ev.direction, 0.5)
                self._log(f"     ↳ [TESTNET] TP1 half-close @ {fill:.5g}",
                          also_print=False)
            else:
                # TP2/SL/TRAIL/TIMEOUT close the entire remaining position.
                fill = tn.close_market(ev.symbol, ev.direction, 1.0)
                self._log(f"     ↳ [TESTNET] {ev.exit_type} full-close @ {fill:.5g}",
                          also_print=False)

        if ev.exit_type == "TP1":
            self.run_tp1 += 1
        elif ev.exit_type == "TP2":
            self.run_tp2 += 1
        elif ev.exit_type == "SL":
            self.run_sl += 1
            self.daily_sl_count += 1
            if self.daily_sl_count >= DAILY_SL_LIMIT:
                self.daily_freeze = True
                self._log(f"  ⚠️  Daily SL limit reached — freezing {symbol} entries today")
        elif ev.exit_type == "TRAIL":
            self.run_trail += 1
        elif ev.exit_type == "TIMEOUT":
            self.run_tmo += 1

        self._append_trade(ev, bar_dt, ev.direction)

    # ── Bar processing · mean-reversion sleeve ────────────────────────────────

    def _run_mr_sleeve(self, symbol: str, s: SymbolState, reading: "_Reading",
                       bar_dt: datetime, reg: str, block_new: bool) -> None:
        """FAZ 2 range mean-reversion, NEUTRAL-only. Off since 2026-08-27."""
        if not MR_ENABLED:
            return

        mr = self.mr_states[symbol]
        # NETTING GUARD (Gemini risk-audit): don't open MR on a symbol that
        # already has an active momentum position — the exchange would net both
        # LONGs into one, and an MR close would shut the momentum leg.
        mr_block = block_new or (s.state != IDLE)
        mr_events = mr.process_bar(
            reading.snap, reading.high, reading.low, regime=reg,
            block_new=mr_block,
            size_mult=self.size_factor)   # FIX #3: equity-throttle aware

        tn = self.testnet_om
        for ev in mr_events:
            self.balance += ev.pnl
            if ev.exit_type == "OPEN":
                self._log(f"  🔁 MR OPEN    {ev.symbol} @ {ev.entry:.5g} "
                          f"| notional=${mr.notional:.0f} | bal=${self.balance:.2f}")
                # FIX #2: MR was sim-only — now mirrored to testnet (LONG-only).
                # NETTING CAVEAT: if a momentum FULL long is already open on this
                # symbol the exchange nets both into one position (rare: momentum
                # fires in BULL, MR in NEUTRAL — regimes seldom overlap same-bar).
                # Flagged for the exec-audit role before any live promotion.
                if tn:
                    fill = tn.open_market(ev.symbol, "LONG", mr.notional, ev.entry)
                    self._log(f"     ↳ [TESTNET] MR order filled @ {fill:.5g}",
                              also_print=False)
                self._log_leg(ev, bar_dt, "MR")   # T0: entry fee
                continue

            emoji = "✅" if ev.pnl > 0 else "❌"
            self._log(f"  {emoji} MR {ev.exit_type:<4}  {ev.symbol} "
                      f"entry={ev.entry:.5g} exit={ev.exit:.5g} "
                      f"pnl=${ev.pnl:+.2f} | bal=${self.balance:.2f}")
            if tn:   # FIX #2: close the testnet MR position (full)
                fill = tn.close_market(ev.symbol, "LONG", 1.0)
                self._log(f"     ↳ [TESTNET] MR {ev.exit_type} close @ {fill:.5g}",
                          also_print=False)

            if ev.exit_type == "TP":
                self.run_mr_tp += 1
            elif ev.exit_type == "SL":
                self.run_mr_sl += 1
            elif ev.exit_type == "TIMEOUT":
                self.run_mr_tmo += 1

            self._append_trade(ev, bar_dt, "MR")

    def _append_trade(self, ev, bar_dt: datetime, direction: str) -> None:
        """Record a closed round-trip. This list is what the track record reads."""
        self.trade_log.append({
            "ts":        bar_dt.isoformat(),
            "symbol":    ev.symbol,
            "direction": direction,
            "exit_type": ev.exit_type,
            "entry":     ev.entry,
            "exit":      ev.exit,
            "pnl":       round(ev.pnl, 4),
            "balance":   round(self.balance, 4),
        })

    # ── Bar processing · telemetry ────────────────────────────────────────────

    def _log_bar_summary(self, bar_dt: datetime, gate_str: str) -> None:
        ret_pct = (self.balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100
        active  = [
            f"{t}:{s.state[0]}"  # T=TEST_OPEN, S=SCALE_OPEN, R=TRAILING
            for t, s in self.sym_states.items()
            if s.state != IDLE
        ]
        self._log(
            f"Bar #{self.bar_count:,} {bar_dt.strftime('%H:%M')} UTC | "
            f"${self.balance:.2f} ({ret_pct:+.2f}%) | "
            f"open={self._open_full_count()}/{MAX_OPEN} "
            f"[{' '.join(active) if active else '—'}] | "
            f"{gate_str}"
        )

    def _accumulate_funnel(self, funnel: dict) -> None:
        """Fold this bar's funnel into the totals and log it if anything happened.

        Without this telemetry a parameter sweep is a blind shot: we could not
        tell whether the live bot trades less than the backtest because the
        regime differs or because a gate behaves differently in production.
        """
        for k, v in funnel.items():
            self.funnel_totals[k] += v

        if not any(funnel[k] for k in ("probe", "confirm_ok",
                                       "confirm_fail", "blocked_max_open")):
            return                       # keep the log readable on quiet bars

        ft = self.funnel_totals
        decided = ft["confirm_ok"] + ft["confirm_fail"]
        cr = (100 * ft["confirm_ok"] / decided) if decided else 0.0
        self._log(
            f"  📊 funnel bar[scan={funnel['scanned']} regime_ok={funnel['regime_pass']} "
            f"probe={funnel['probe']} conf_ok={funnel['confirm_ok']} "
            f"conf_fail={funnel['confirm_fail']} full={funnel['full']} "
            f"maxopen_block={funnel['blocked_max_open']}] | "
            f"total[probe={ft['probe']} conf={cr:.0f}% full={ft['full']}] | "
            f"probe_cost=${self.probe_cost:+.2f}"
        )


    # ── Main entry point ──────────────────────────────────────────────────────

    def run(self) -> None:
        mode = "TESTNET live orders" if self.testnet_om else "local simulation"
        self._log("=" * 60)
        self._log(f"BreakoutBot Paper Trader — {len(self.tokens)} symbols | {mode}")
        self._log(
            f"Balance: ${self.balance:.2f} | MAX_OPEN: {MAX_OPEN} | "
            f"Session: {SESSION_START_UTC:02d}–{SESSION_END_UTC:02d} UTC"
        )
        if self.testnet_om:
            tb = self.testnet_om.get_balance()
            self._log(f"Testnet account balance: ${tb:.2f} USDT")
        self._log("=" * 60)

        while True:
            try:
                bar_dt = wait_for_bar_close()
                self._process_bar(bar_dt)
            except KeyboardInterrupt:
                self._log("\nStopped by user (Ctrl-C).")
                self._save_state()
                self._print_status()
                self.log_f.close()
                break
            except Exception as exc:
                self._log(f"❌ Unhandled error: {exc}")
                import traceback
                traceback.print_exc()
                time.sleep(30)   # brief pause before retrying


# ── Status-only mode ──────────────────────────────────────────────────────────

def print_saved_status() -> None:
    """Read state_paper.json and pretty-print the current status."""
    if not os.path.exists(STATE_FILE):
        print(f"No state file found ({STATE_FILE}). Run paper_bb.py first.")
        return
    with open(STATE_FILE) as f:
        st = json.load(f)

    balance    = st["balance"]
    initial    = INITIAL_BALANCE
    ret_pct    = (balance - initial) / initial * 100
    peak       = st["peak"]
    peak_dd    = (balance - peak) / peak * 100 if peak > 0 else 0.0
    bar_count  = st["bar_count"]
    daily_day  = st["daily_day"]
    daily_freeze = st["daily_freeze"]
    trade_log  = st.get("trade_log", [])

    closed = [t for t in trade_log if t.get("exit_type") not in ("OPEN",)]
    ps     = position_stats(aggregate_positions(closed),
                            risk_per_trade=RISK_PER_TRADE_USD)

    print("=" * 60)
    print("  BreakoutBot Paper Status")
    print(f"  Day: {daily_day} | Bar: #{bar_count:,} | Freeze: {daily_freeze}")
    print(f"  Balance: ${balance:.2f} ({ret_pct:+.2f}%) | PeakDD: {peak_dd:.1f}%")
    print(f"  Positions: {ps['n_positions']} ({ps['n_wins']}W/{ps['n_losses']}L) | "
          f"WR: {ps['win_rate']}% (break-even {ps['breakeven_wr']}%) | PF: {ps['profit_factor']}")
    print(f"  Avg win/loss: ${ps['avg_win']:+.2f}/${ps['avg_loss']:+.2f} | "
          f"payoff {ps['payoff']} | expectancy ${ps['expectancy']:+.2f}/pos")
    ft = st.get("funnel_totals") or {}
    if ft:
        ok, fail = ft.get("confirm_ok", 0), ft.get("confirm_fail", 0)
        cr = 100 * ok / (ok + fail) if (ok + fail) else 0.0
        print(f"  Funnel: probe={ft.get('probe',0)} confirm={cr:.0f}% "
              f"full={ft.get('full',0)} maxopen_block={ft.get('blocked_max_open',0)} | "
              f"probe drag: ${st.get('probe_cost', 0.0):+.2f}")
    print("=" * 60)

    # Open positions
    sym_states = st.get("sym_states", {})
    active = {t: s for t, s in sym_states.items() if s["state"] != IDLE}
    if active:
        print("  Open positions:")
        for tok, s in active.items():
            print(f"    {tok}: {s['state']} | dir={s['direction']} "
                  f"| entry={s['full_entry']:.5g} | bars={s['bars_held']}")
    else:
        print("  No open positions")

    # Recent trades
    if trade_log:
        print(f"\n  Last {min(10, len(trade_log))} full trades:")
        for t in trade_log[-10:]:
            emoji = "✅" if t["pnl"] > 0 else "❌"
            print(f"    {emoji} {t['ts'][:16]} {t['symbol']:15s} [{t['exit_type']:<8}] "
                  f"pnl=${t['pnl']:+.2f} → bal=${t['balance']:.2f}")
    print("=" * 60)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="BreakoutBot Paper Trader")
    ap.add_argument("--tokens", nargs="+", default=None,
                    help="Subset of tokens to trade (default: all from config.py)")
    ap.add_argument("--resume", action="store_true",
                    help="Resume from saved state_paper.json")
    ap.add_argument("--status", action="store_true",
                    help="Print current paper trading status and exit")
    ap.add_argument("--testnet", action="store_true",
                    help="Place real orders on Binance Futures Testnet (requires secrets_local.py)")
    args = ap.parse_args()

    if args.status:
        print_saved_status()
        return

    tokens = args.tokens if args.tokens else TOKENS
    unknown = [t for t in tokens if t not in TOKENS]
    if unknown:
        print(f"⚠️  Unknown tokens (not in config.py): {unknown}")

    # ── Testnet setup ─────────────────────────────────────────────────────────
    testnet_om = None
    if args.testnet:
        try:
            from secrets_local import TESTNET_API_KEY, TESTNET_API_SECRET
        except ImportError:
            print("❌ secrets_local.py bulunamadı.")
            print("   1. cp secrets_local.py'yi oluştur ve API key'leri gir")
            print("   2. https://testnet.binancefuture.com → API Management")
            sys.exit(1)

        if TESTNET_API_KEY == "BURAYA_KOPYALA":
            print("❌ secrets_local.py içindeki API key henüz doldurulmamış.")
            print("   https://testnet.binancefuture.com → API Management → Generate Key")
            sys.exit(1)

        print("🔗 Binance Futures Testnet'e bağlanılıyor…")
        testnet_om = TestnetOrderManager(TESTNET_API_KEY, TESTNET_API_SECRET)
        bal = testnet_om.get_balance()
        print(f"   Testnet bakiyesi: ${bal:.2f} USDT")
        print(f"   {len(tokens)} sembol için kaldıraç + margin ayarlanıyor…")
        testnet_om.setup_symbols(tokens)
        print("   ✅ Testnet hazır — emirler testnet'e gidecek\n")

    trader = PaperTrader(tokens=tokens, resume=args.resume, testnet_om=testnet_om)
    trader.run()


if __name__ == "__main__":
    main()
