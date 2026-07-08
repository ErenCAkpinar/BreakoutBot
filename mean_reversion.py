"""
FAZ 2 — Range Mean-Reversion sleeve.

A second, uncorrelated strategy that runs in PARALLEL to the momentum-breakout
engine (strategy.SymbolState). Momentum buys breakouts; this fades oversold dips
back to the band mean — but ONLY in a ranging market, where breakouts chop out and
mean-reversion has edge.

Activation (all must hold, long-only in Faz 2):
    regime == "NEUTRAL"            (directional gate says "no trend bias")
    ADX(14)  <  MR_ADX_MAX         (no trend strength = genuine range)
    RSI(14)  <  MR_RSI_OVERSOLD    (oversold)
    bb_pos   <= MR_BB_POS_MAX      (price in bottom of the Bollinger band)

Exit:
    TP  = band mean = (bb_upper + bb_lower)/2  (reversion to the middle)
    SL  = entry − MR_SL_ATR × ATR              (range broke down)
    TMO = MR_TIMEOUT_BARS bars                 (no reversion → stand aside)

Sizing mirrors the momentum risk model but at half the risk ($5 vs $10):
    notional = MR_RISK_PER_TRADE_USD / sl_frac, clamped [MIN, MAX].

Produces the SAME strategy.Trade objects (kind="MR") so the backtest accounts for
PnL and counts trades through one uniform path.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import (
    EXEC_COST_PER_SIDE,
    MR_ADX_MAX, MR_RSI_OVERSOLD, MR_BB_POS_MAX,
    MR_SL_ATR, MR_TIMEOUT_BARS, MR_COOLDOWN_BARS,
    MR_RISK_PER_TRADE_USD, MR_MIN_NOTIONAL_USD, MR_MAX_NOTIONAL_USD,
)
from strategy import Trade   # reuse the exact same dataclass

MR_IDLE = "MR_IDLE"
MR_OPEN = "MR_OPEN"


@dataclass
class MRState:
    """Per-symbol mean-reversion state machine (long-only, Faz 2)."""
    symbol:   str
    state:    str   = MR_IDLE
    entry:    float = 0.0
    notional: float = 0.0
    sl:       float = 0.0
    tp:       float = 0.0
    bars_in:  int   = 0
    cooldown: int   = 0

    def process_bar(self, snapshot: dict, high: float, low: float,
                    regime: str = "NEUTRAL", block_new: bool = False,
                    size_mult: float = 1.0) -> list[Trade]:
        events: list[Trade] = []
        ind   = snapshot["indicators"]
        price = snapshot["price"]["current"]

        if self.cooldown > 0:
            self.cooldown -= 1

        # ── MR_IDLE: scan for an oversold-in-range setup ──────────────────────
        if self.state == MR_IDLE:
            if block_new or self.cooldown > 0:
                return events

            adx_val  = ind["adx"]
            rsi_val  = ind["rsi_14"]
            bb_pos   = ind["bb_position"]
            bb_upper = ind["bb_upper"]
            bb_lower = ind["bb_lower"]
            atr_val  = ind["atr_14"]

            ranging  = (regime == "NEUTRAL") and (adx_val < MR_ADX_MAX)
            oversold = (rsi_val < MR_RSI_OVERSOLD) and (bb_pos <= MR_BB_POS_MAX)
            mid_bb   = (bb_upper + bb_lower) / 2.0

            # TP must sit above entry (mean above an oversold price) and ATR valid
            if ranging and oversold and atr_val > 0 and price > 0 and mid_bb > price:
                sl_dist  = atr_val * MR_SL_ATR
                sl_frac  = sl_dist / price
                notional = MR_RISK_PER_TRADE_USD / sl_frac if sl_frac > 0 else MR_MIN_NOTIONAL_USD
                notional = max(MR_MIN_NOTIONAL_USD, min(notional, MR_MAX_NOTIONAL_USD))
                notional *= size_mult   # equity throttle (1.0 normal · 0.5 in -7% DD)

                self.state    = MR_OPEN
                self.entry    = price
                self.notional = notional
                self.sl       = price - sl_dist
                self.tp       = mid_bb
                self.bars_in  = 0

                fee = notional * EXEC_COST_PER_SIDE
                events.append(Trade(symbol=self.symbol, direction="LONG", kind="MR",
                                    entry=price, exit=0.0, pnl=-fee, exit_type="OPEN"))
            return events

        # ── MR_OPEN: manage the position (exits checked from the NEXT bar) ────
        if self.state == MR_OPEN:
            self.bars_in += 1
            exit_p:   float | None = None
            exit_type: str  | None = None

            if low <= self.sl:                       # stop first (conservative)
                exit_p, exit_type = self.sl, "SL"
            elif high >= self.tp:                    # reverted to the mean
                exit_p, exit_type = self.tp, "TP"
            elif self.bars_in >= MR_TIMEOUT_BARS:    # no reversion → stand aside
                exit_p, exit_type = price, "TIMEOUT"

            if exit_type is not None:
                gross = self.notional * (exit_p - self.entry) / self.entry   # long: mult=+1
                pnl   = gross - self.notional * EXEC_COST_PER_SIDE
                events.append(Trade(symbol=self.symbol, direction="LONG", kind="MR",
                                    entry=self.entry, exit=exit_p, pnl=pnl, exit_type=exit_type))
                self.state    = MR_IDLE
                self.cooldown = MR_COOLDOWN_BARS

        return events
