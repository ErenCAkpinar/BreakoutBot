"""
BreakoutBot — Strategy: Test-Confirm-Scale execution state machine

State transitions per symbol:
    IDLE
      → (MathEngine STRONG_LONG/SHORT) → TEST_OPEN

    TEST_OPEN  [$20 test position, SL=1×ATR, wait 1 bar]
      → (next bar: confirmed) → SCALE_OPEN
      → (SL hit or confirmation failed) → IDLE + cooldown

    SCALE_OPEN  [$300×3x full position, TP1/TP2/SL]
      → (TP1 hit) → TRAILING  [TP1_CLOSE_FRAC closed (0.0 since Faz 8: nothing);
                               full_sl is set to breakeven but NOT checked in
                               TRAILING — the only stop there is the trail,
                               trail_best − TRAIL_ATR×ATR, which right after TP1
                               sits BELOW entry (e.g. 103 − 3.75 → 99.25 on a
                               100 entry). Deployed behaviour; see metrics.py
                               aggregate_positions note and DEFTER Tur 14.]
      → (SL hit) → IDLE + cooldown

    TRAILING  [remaining position, trailing SL active; no breakeven floor]
      → (TP2 hit) → IDLE
      → (trailing SL hit) → IDLE
      → (timeout 48 bars) → IDLE
"""

from __future__ import annotations

from dataclasses import dataclass, field

from math_engine import MathEngine
from config import (
    TEST_SIZE_USD, FULL_SIZE_USD, LEVERAGE, EXEC_COST_PER_SIDE,
    RISK_PER_TRADE_USD, MIN_NOTIONAL_USD, MAX_NOTIONAL_USD,
    SL_TEST_ATR, SL_FULL_ATR, TP1_ATR, TP2_ATR, TRAIL_ATR, TP1_CLOSE_FRAC,
    TIMEOUT_BARS, COOLDOWN_BARS,
    CONFIRM_PRICE_MOVE_PCT, CONFIRM_VOL_MULT,
    CONFIRM_RSI_LONG_MIN, CONFIRM_RSI_SHORT_MAX,
    HURST_LONG_MIN, ADVERSE_FILLS, MIN_SL_FRAC,
)

# FILL CONVENTION (E7) — this file encodes intrabar fill assumptions in the
# ORDER of its if/elif exit checks. OHLC cannot say whether the high or the low
# of a bar came first, so any bar touching two exit levels is ambiguous. With
# ADVERSE_FILLS (default) every ambiguity resolves AGAINST the position:
#   · SCALE_OPEN: a bar touching both SL and TP1 fills the SL
#   · TRAILING  : exits are tested against the trail level as of the bar's
#                 OPEN (the ratchet moves only on completed bars), and a bar
#                 touching both the trail and TP2 fills the trail
#   · TIMEOUT   : fills at the bar close — the only price a decision made at
#                 the close can actually get (was: bar midpoint, a fantasy)
#   · adverse fills are clamped into the bar's range (gap-through protection)
# X_ADVERSE_FILLS=0 restores the old optimistic ordering for comparison runs.

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
        price = snapshot["price"]["current"]

        # Decrement cooldown
        if self.cooldown > 0:
            self.cooldown -= 1

        # One handler per state; each returns only its own events.
        if self.state == IDLE:
            return self._scan_for_setup(snapshot, price)
        if self.state == TEST_OPEN:
            return self._resolve_probe(price, high, low, rsi_val, vol_ratio,
                                       block_new_full, size_mult)
        if self.state == SCALE_OPEN:
            return self._manage_full_position(price, high, low)
        if self.state == TRAILING:
            return self._manage_trailing(price, high, low)
        return []

    # ── One handler per state of the Test-Confirm-Scale machine ───────────────

    def _scan_for_setup(self, snapshot: dict, price: float) -> list[Trade]:
        """IDLE — look for a STRONG signal and open the $20 probe."""
        events: list[Trade] = []
        if self.cooldown > 0:
            return events
        result = self.engine.analyze(snapshot)
        signal    = result["signal"]
        hurst_val = snapshot["meta"].get("hurst", 0.5)
        atr_val   = snapshot["meta"].get("atr", 0.001 * price)

        # Gate: Hurst filter (block LONG in mean-reverting regime)
        if signal == "STRONG_LONG" and hurst_val < HURST_LONG_MIN:
            return events  # mean-reverting regime — no long

        # Gate: friction floor. The FULL stop would sit sl_frac from price and
        # the round trip costs 0.0015/sl_frac of the risk taken. Too tight a
        # stop is structurally expensive no matter how good the signal, so the
        # setup is refused here — before the probe's fee is paid.
        if MIN_SL_FRAC > 0 and price > 0 and \
           (atr_val * SL_FULL_ATR / price) < MIN_SL_FRAC:
            return events

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
        return events
    def _resolve_probe(self, price: float, high: float, low: float,
                       rsi_val: float, vol_ratio: float,
                       block_new_full: bool, size_mult: float) -> list[Trade]:
        """TEST_OPEN — a bar later the probe is stopped, fails, or scales up."""
        events: list[Trade] = []
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
        return events
    def _manage_full_position(self, price: float, high: float,
                              low: float) -> list[Trade]:
        """SCALE_OPEN — the full position, until TP1 hands it to the trail."""
        events: list[Trade] = []
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

        # E7: a bar touching BOTH levels is ambiguous — adverse fill wins.
        # (Needs a ~3.5×ATR bar: SL 1.5×ATR below entry + TP1 2×ATR above.)
        if ADVERSE_FILLS and sl_hit:
            tp1_hit = False

        if tp1_hit:
            exit_p  = self.full_tp1
            # Scale-out fraction is configurable (TP1_CLOSE_FRAC, default 0.50).
            # At 0.0 nothing is banked here and the whole position rides the
            # trail — which is the point of the E1 experiment: partial exits
            # cap winners near 0.7R while a stop-out still costs a full 1R.
            frac = TP1_CLOSE_FRAC
            if frac > 0.0:
                part  = self.full_notional * frac
                gross = part * (exit_p - self.full_entry) / self.full_entry * mult
                pnl   = gross - part * EXEC_COST_PER_SIDE
                t = Trade(symbol=self.symbol, direction=self.direction, kind="FULL",
                          entry=self.full_entry, exit=exit_p, pnl=pnl, exit_type="TP1")
                events.append(t)
                self.trades.append(t)

            # Move to TRAILING with whatever is left
            self.state       = TRAILING
            self.full_notional = self.full_notional * (1.0 - frac)
            self.full_sl     = self.full_entry     # SL → breakeven
            self.tp1_hit     = True
            self.bars_held   = 0

        elif sl_hit:
            exit_p = self.full_sl
            if ADVERSE_FILLS:
                # Gap-through: a bar entirely beyond the stop cannot fill AT
                # the stop — clamp the fill into the bar's traded range.
                exit_p = min(exit_p, high) if self.direction == "LONG" else max(exit_p, low)
            gross  = self.full_notional * (exit_p - self.full_entry) / self.full_entry * mult
            pnl    = gross - self.full_notional * EXEC_COST_PER_SIDE
            t = Trade(symbol=self.symbol, direction=self.direction, kind="FULL",
                      entry=self.full_entry, exit=exit_p, pnl=pnl, exit_type="SL")
            events.append(t)
            self.trades.append(t)
            self._reset(cooldown=COOLDOWN_BARS)

        elif timed_out:
            # E7: the timeout decision exists only at bar close — fill there.
            exit_p = price if ADVERSE_FILLS else (high + low) / 2
            gross  = self.full_notional * (exit_p - self.full_entry) / self.full_entry * mult
            pnl    = gross - self.full_notional * EXEC_COST_PER_SIDE
            t = Trade(symbol=self.symbol, direction=self.direction, kind="FULL",
                      entry=self.full_entry, exit=exit_p, pnl=pnl, exit_type="TIMEOUT")
            events.append(t)
            self.trades.append(t)
            self._reset(cooldown=COOLDOWN_BARS)
        return events
    def _manage_trailing(self, price: float, high: float,
                         low: float) -> list[Trade]:
        """TRAILING — what is left rides the ratchet to TP2, the trail, or time."""
        events: list[Trade] = []
        self.bars_held += 1
        mult = 1 if self.direction == "LONG" else -1

        if not ADVERSE_FILLS:
            # Old optimistic convention: raise the trail from THIS bar's
            # high, then test the SAME bar's low against the raised level —
            # an exit that assumes the high always precedes the low.
            best_now = high if self.direction == "LONG" else low
            if (self.direction == "LONG"  and best_now > self.trail_best) or \
               (self.direction == "SHORT" and best_now < self.trail_best):
                self.trail_best = best_now

        # Trailing SL = best_price ∓ TRAIL_ATR × atr
        # E7 (adverse): trail_best here contains only COMPLETED bars — this
        # bar's exits are tested against the level as of its OPEN, and the
        # ratchet is raised at the bottom of this branch only if we survive.
        trail_sl = self.trail_best - mult * self.test_atr * TRAIL_ATR

        # Check exits
        tp2_hit   = (self.direction == "LONG"  and high >= self.full_tp2) or \
                    (self.direction == "SHORT" and low  <= self.full_tp2)
        trail_hit = (self.direction == "LONG"  and low  <= trail_sl) or \
                    (self.direction == "SHORT" and high >= trail_sl)
        timed_out = self.bars_held >= TIMEOUT_BARS

        # E7: a bar touching BOTH the trail and TP2 is ambiguous — the
        # adverse fill (trail) wins.
        if ADVERSE_FILLS and trail_hit:
            tp2_hit = False

        if tp2_hit:
            exit_p = self.full_tp2
            exit_type = "TP2"
        elif trail_hit:
            exit_p = trail_sl
            if ADVERSE_FILLS:
                # Gap-through: clamp the fill into the bar's traded range.
                exit_p = min(exit_p, high) if self.direction == "LONG" else max(exit_p, low)
            exit_type = "TRAIL"
        elif timed_out:
            # E7: the timeout decision exists only at bar close — fill there.
            exit_p = price if ADVERSE_FILLS else (high + low) / 2
            exit_type = "TIMEOUT"
        else:
            if ADVERSE_FILLS:
                # Survived the bar — NOW the completed bar raises the ratchet.
                best_now = high if self.direction == "LONG" else low
                if (self.direction == "LONG"  and best_now > self.trail_best) or \
                   (self.direction == "SHORT" and best_now < self.trail_best):
                    self.trail_best = best_now
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
