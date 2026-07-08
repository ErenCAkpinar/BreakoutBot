"""
BreakoutBot — Strategy: Test-Confirm-Scale execution state machine

State transitions per symbol:
    IDLE
      → (MathEngine STRONG_LONG/SHORT) → TEST_OPEN

    TEST_OPEN  [$20 test position, SL=1×ATR, wait 1 bar]
      → (next bar: confirmed) → SCALE_OPEN
      → (SL hit or confirmation failed) → IDLE + cooldown

    SCALE_OPEN  [$300×3x full position, TP1/TP2/SL]
      → (TP1 hit) → TRAILING  [50% closed, SL → breakeven]
      → (SL hit) → IDLE + cooldown

    TRAILING  [remaining 50%, trailing SL active]
      → (TP2 hit) → IDLE
      → (trailing SL hit) → IDLE
      → (timeout 48 bars) → IDLE
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from math_engine import MathEngine
from config import (
    TEST_SIZE_USD, FULL_SIZE_USD, LEVERAGE, EXEC_COST_PER_SIDE,
    RISK_PER_TRADE_USD, MIN_NOTIONAL_USD, MAX_NOTIONAL_USD,
    SL_TEST_ATR, SL_FULL_ATR, TP1_ATR, TP2_ATR, TRAIL_ATR,
    TIMEOUT_BARS, COOLDOWN_BARS,
    CONFIRM_PRICE_MOVE_PCT, CONFIRM_VOL_MULT,
    CONFIRM_RSI_LONG_MIN, CONFIRM_RSI_SHORT_MAX,
    HURST_LONG_MIN,
)

# State constants
IDLE       = "IDLE"
TEST_OPEN  = "TEST_OPEN"
SCALE_OPEN = "SCALE_OPEN"
TRAILING   = "TRAILING"


@dataclass
class Trade:
    """Record of a completed trade (test or full)."""
    symbol:    str
    direction: str         # "LONG" or "SHORT"
    kind:      str         # "TEST" or "FULL"
    entry:     float
    exit:      float
    pnl:       float
    exit_type: str         # "TP1", "TP2", "SL", "TRAIL", "TIMEOUT", "CONFIRM_FAIL"


@dataclass
class SymbolState:
    """Tracks the execution state for a single symbol."""

    symbol: str
    engine: MathEngine = field(default_factory=MathEngine, repr=False)

    # State machine
    state:        str   = IDLE
    direction:    str   = ""     # "LONG" or "SHORT"
    cooldown:     int   = 0      # bars remaining before new signals allowed

    # Test position
    test_entry:   float = 0.0
    test_sl:      float = 0.0
    test_atr:     float = 0.0

    # Full position
    full_entry:   float = 0.0
    full_sl:      float = 0.0
    full_tp1:     float = 0.0
    full_tp2:     float = 0.0
    full_notional:float = 0.0
    tp1_hit:      bool  = False
    be_price:     float = 0.0    # breakeven after TP1
    trail_best:   float = 0.0    # best price seen (for trailing SL)
    bars_held:    int   = 0

    # Statistics
    trades:       list  = field(default_factory=list)

    # ── Main bar processor ────────────────────────────────────────────────────

    def process_bar(self, snapshot: dict, high: float, low: float,
                    rsi_val: float, vol_ratio: float,
                    block_new_full: bool = False,
                    size_mult: float = 1.0) -> list[Trade]:
        """
        Process one 5m bar. Returns list of completed Trade objects.
        Call this for every bar, in order.

        block_new_full : when True and confirmation succeeds, suppress the SCALE_OPEN
                         (used by paper trader to enforce MAX_OPEN across all symbols).
        """
        events: list[Trade] = []
        price = snapshot["price"]["current"]

        # Decrement cooldown
        if self.cooldown > 0:
            self.cooldown -= 1

        # ── IDLE: scan for new signal ──────────────────────────────────────
        if self.state == IDLE:
            if self.cooldown > 0:
                return events
            result = self.engine.analyze(snapshot)
            signal    = result["signal"]
            composite = result["composite_score"]
            hurst_val = snapshot["meta"].get("hurst", 0.5)
            atr_val   = snapshot["meta"].get("atr", 0.001 * price)

            # Gate: Hurst filter (block LONG in mean-reverting regime)
            if signal == "STRONG_LONG" and hurst_val < HURST_LONG_MIN:
                return events  # mean-reverting regime — no long

            if signal in ("STRONG_LONG", "STRONG_SHORT"):
                direction = "LONG" if signal == "STRONG_LONG" else "SHORT"
                mult      = 1 if direction == "LONG" else -1
                sl_dist   = atr_val * SL_TEST_ATR

                self.state      = TEST_OPEN
                self.direction  = direction
                self.test_entry = price
                self.test_atr   = atr_val
                self.test_sl    = price - mult * sl_dist
                self.bars_held  = 0

                # Deduct entry fee for test position
                fee = TEST_SIZE_USD * EXEC_COST_PER_SIDE
                events.append(Trade(
                    symbol=self.symbol, direction=direction, kind="TEST",
                    entry=price, exit=0.0, pnl=-fee, exit_type="OPEN"))

        # ── TEST_OPEN: wait 1 bar, then check confirmation ─────────────────
        elif self.state == TEST_OPEN:
            self.bars_held += 1
            mult = 1 if self.direction == "LONG" else -1

            # Check test SL first
            sl_hit = (self.direction == "LONG"  and low  <= self.test_sl) or \
                     (self.direction == "SHORT" and high >= self.test_sl)

            if sl_hit:
                exit_p = self.test_sl
                gross  = TEST_SIZE_USD * (exit_p - self.test_entry) / self.test_entry * mult
                pnl    = gross - TEST_SIZE_USD * EXEC_COST_PER_SIDE
                t = Trade(symbol=self.symbol, direction=self.direction, kind="TEST",
                          entry=self.test_entry, exit=exit_p,
                          pnl=pnl, exit_type="SL")
                events.append(t)
                self.trades.append(t)
                self._reset(cooldown=COOLDOWN_BARS)
                return events

            # After 1 bar: run confirmation check
            if self.bars_held >= 1:
                confirmed = self._confirm(price, rsi_val, vol_ratio, mult)

                # Close test position (either way)
                gross = TEST_SIZE_USD * (price - self.test_entry) / self.test_entry * mult
                pnl   = gross - TEST_SIZE_USD * EXEC_COST_PER_SIDE
                t = Trade(symbol=self.symbol, direction=self.direction, kind="TEST",
                          entry=self.test_entry, exit=price,
                          pnl=pnl, exit_type="CONFIRM_OK" if confirmed else "CONFIRM_FAIL")
                events.append(t)
                self.trades.append(t)

                # size_mult>0 guard (Gemini risk-audit): if regime flipped to
                # NEUTRAL/BEAR between the TEST bar and this confirm bar, size_mult
                # is 0 → opening FULL would create a $0-notional position (and a
                # rejected testnet order). Skip → fall to else → reset + cooldown.
                if confirmed and not block_new_full and size_mult > 0:
                    # Open full position
                    atr_val  = self.test_atr
                    sl_dist  = atr_val * SL_FULL_ATR
                    tp1_dist = atr_val * TP1_ATR
                    tp2_dist = atr_val * TP2_ATR
                    # Risk-based sizing: SL costs ~RISK_PER_TRADE_USD regardless of coin ATR
                    sl_frac  = sl_dist / price if price > 0 else 0.01
                    notional = (RISK_PER_TRADE_USD / sl_frac) if sl_frac > 0 else FULL_SIZE_USD * LEVERAGE
                    notional = max(MIN_NOTIONAL_USD, min(notional, MAX_NOTIONAL_USD))
                    notional *= size_mult   # FAZ 1: regime throttle (BULL 1.0 / NEUTRAL+BEAR 0.35)

                    self.state        = SCALE_OPEN
                    self.full_entry   = price
                    self.full_notional= notional
                    self.full_sl      = price - mult * sl_dist
                    self.full_tp1     = price + mult * tp1_dist
                    self.full_tp2     = price + mult * tp2_dist
                    self.be_price     = price   # will update to entry after TP1
                    self.trail_best   = price
                    self.tp1_hit      = False
                    self.bars_held    = 0

                    fee = notional * EXEC_COST_PER_SIDE
                    events.append(Trade(
                        symbol=self.symbol, direction=self.direction, kind="FULL",
                        entry=price, exit=0.0, pnl=-fee, exit_type="OPEN"))
                else:
                    # confirmed but MAX_OPEN reached (block_new_full=True), or failed confirmation
                    self._reset(cooldown=COOLDOWN_BARS)

        # ── SCALE_OPEN: manage full position ──────────────────────────────
        elif self.state == SCALE_OPEN:
            self.bars_held += 1
            mult = 1 if self.direction == "LONG" else -1

            # Update trail best price
            best_now = high if self.direction == "LONG" else low
            if (self.direction == "LONG"  and best_now > self.trail_best) or \
               (self.direction == "SHORT" and best_now < self.trail_best):
                self.trail_best = best_now

            # Check TP1
            tp1_hit = (self.direction == "LONG"  and high >= self.full_tp1) or \
                      (self.direction == "SHORT" and low  <= self.full_tp1)
            # Check SL
            sl_hit  = (self.direction == "LONG"  and low  <= self.full_sl) or \
                      (self.direction == "SHORT" and high >= self.full_sl)
            # Timeout
            timed_out = self.bars_held >= TIMEOUT_BARS

            if tp1_hit:
                exit_p  = self.full_tp1
                half    = self.full_notional * 0.50
                gross   = half * (exit_p - self.full_entry) / self.full_entry * mult
                pnl     = gross - half * EXEC_COST_PER_SIDE
                t = Trade(symbol=self.symbol, direction=self.direction, kind="FULL",
                          entry=self.full_entry, exit=exit_p, pnl=pnl, exit_type="TP1")
                events.append(t)
                self.trades.append(t)

                # Move to TRAILING with remaining 50%
                self.state       = TRAILING
                self.full_notional = self.full_notional * 0.50
                self.full_sl     = self.full_entry     # SL → breakeven
                self.tp1_hit     = True
                self.bars_held   = 0

            elif sl_hit:
                exit_p = self.full_sl
                gross  = self.full_notional * (exit_p - self.full_entry) / self.full_entry * mult
                pnl    = gross - self.full_notional * EXEC_COST_PER_SIDE
                t = Trade(symbol=self.symbol, direction=self.direction, kind="FULL",
                          entry=self.full_entry, exit=exit_p, pnl=pnl, exit_type="SL")
                events.append(t)
                self.trades.append(t)
                self._reset(cooldown=COOLDOWN_BARS)

            elif timed_out:
                exit_p = (high + low) / 2
                gross  = self.full_notional * (exit_p - self.full_entry) / self.full_entry * mult
                pnl    = gross - self.full_notional * EXEC_COST_PER_SIDE
                t = Trade(symbol=self.symbol, direction=self.direction, kind="FULL",
                          entry=self.full_entry, exit=exit_p, pnl=pnl, exit_type="TIMEOUT")
                events.append(t)
                self.trades.append(t)
                self._reset(cooldown=COOLDOWN_BARS)

        # ── TRAILING: remaining 50%, trailing SL and TP2 ──────────────────
        elif self.state == TRAILING:
            self.bars_held += 1
            mult = 1 if self.direction == "LONG" else -1

            # Update best price
            best_now = high if self.direction == "LONG" else low
            if (self.direction == "LONG"  and best_now > self.trail_best) or \
               (self.direction == "SHORT" and best_now < self.trail_best):
                self.trail_best = best_now

            # Trailing SL = best_price ∓ TRAIL_ATR × atr
            trail_sl = self.trail_best - mult * self.test_atr * TRAIL_ATR

            # Check exits
            tp2_hit   = (self.direction == "LONG"  and high >= self.full_tp2) or \
                        (self.direction == "SHORT" and low  <= self.full_tp2)
            trail_hit = (self.direction == "LONG"  and low  <= trail_sl) or \
                        (self.direction == "SHORT" and high >= trail_sl)
            timed_out = self.bars_held >= TIMEOUT_BARS

            if tp2_hit:
                exit_p = self.full_tp2
                exit_type = "TP2"
            elif trail_hit:
                exit_p = trail_sl
                exit_type = "TRAIL"
            elif timed_out:
                exit_p = (high + low) / 2
                exit_type = "TIMEOUT"
            else:
                return events  # still holding

            gross = self.full_notional * (exit_p - self.full_entry) / self.full_entry * mult
            pnl   = gross - self.full_notional * EXEC_COST_PER_SIDE
            t = Trade(symbol=self.symbol, direction=self.direction, kind="FULL",
                      entry=self.full_entry, exit=exit_p, pnl=pnl, exit_type=exit_type)
            events.append(t)
            self.trades.append(t)
            self._reset(cooldown=COOLDOWN_BARS)

        return events

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _confirm(self, price: float, rsi_val: float, vol_ratio: float, mult: int) -> bool:
        """Check all 3 confirmation criteria."""
        price_moved  = (price - self.test_entry) * mult > self.test_entry * CONFIRM_PRICE_MOVE_PCT
        vol_ok       = vol_ratio >= CONFIRM_VOL_MULT
        rsi_ok       = (rsi_val >= CONFIRM_RSI_LONG_MIN  if self.direction == "LONG"
                        else rsi_val <= CONFIRM_RSI_SHORT_MAX)
        return price_moved and vol_ok and rsi_ok

    def _reset(self, cooldown: int = 0) -> None:
        self.state      = IDLE
        self.direction  = ""
        self.cooldown   = cooldown
        self.bars_held  = 0
        self.test_entry = 0.0
        self.full_entry = 0.0
        self.trail_best = 0.0
        self.tp1_hit    = False
