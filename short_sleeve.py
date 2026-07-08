"""
FAZ 3 — Short sleeve (separate, strict price-action shorts).

WHY a separate engine: the Wave-11 MathEngine is long-biased and emits ~0
STRONG_SHORT signals (validated: 0 shorts / 131 trades over 90d). Research
rejected loosening the 33 threshold (it would bolt weak signals onto the
unbounded-loss side). So shorts get their own strict, multi-condition entry —
NOT the composite score.

Entry (ALL must hold — shorts are the risky side, so be picky):
    regime ∈ {BEAR, NEUTRAL}        (BULL → risk 0 → never short an uptrend)
    ema_alignment == "BEARISH"      (ema9<ema21<ema50 = lower highs / downtrend)
    macd_histogram  < 0             (bearish momentum)
    SHORT_RSI_FLOOR < RSI < SHORT_RSI_MAX   (bearish, but not bounce-prone)
    ADX  > SHORT_ADX_MIN            (a real trend, not chop)
    bb_pos < SHORT_BB_POS_MAX       (price breaking down through the band)
    vol_ratio > SHORT_VOL_MIN       (volume conviction — OI-rise proxy)

Sizing (regime-gated risk, §9):   BEAR $8 · NEUTRAL $5 · BULL closed.
Exit: mirrors the momentum SCALE machinery, short side —
    SL  = entry + SHORT_SL_ATR×ATR
    TP1 = entry − SHORT_TP1_ATR×ATR → close 50%, SL→breakeven, start trailing
    TP2 = entry − SHORT_TP2_ATR×ATR
    TRAIL = trail_best(low) + SHORT_TRAIL_ATR×ATR
    TIMEOUT after SHORT_TIMEOUT_BARS.

Emits strategy.Trade objects (kind="SHORT") for uniform backtest accounting.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import (
    EXEC_COST_PER_SIDE,
    SHORT_RISK_USD, SHORT_MIN_NOTIONAL_USD, SHORT_MAX_NOTIONAL_USD,
    SHORT_ADX_MIN, SHORT_RSI_MAX, SHORT_RSI_FLOOR, SHORT_BB_POS_MAX, SHORT_VOL_MIN,
    SHORT_SL_ATR, SHORT_TP1_ATR, SHORT_TP2_ATR, SHORT_TRAIL_ATR,
    SHORT_TIMEOUT_BARS, SHORT_COOLDOWN_BARS,
)
from strategy import Trade   # reuse the exact same dataclass

S_IDLE  = "S_IDLE"
S_FULL  = "S_FULL"     # full position open (pre-TP1)
S_TRAIL = "S_TRAIL"    # 50% closed, trailing the remainder


@dataclass
class ShortState:
    """Per-symbol strict short state machine (short-only)."""
    symbol:   str
    state:    str   = S_IDLE
    entry:    float = 0.0
    notional: float = 0.0
    atr:      float = 0.0
    sl:       float = 0.0
    tp1:      float = 0.0
    tp2:      float = 0.0
    best:     float = 0.0     # lowest low seen (best for a short)
    bars:     int   = 0
    cooldown: int   = 0

    def process_bar(self, snapshot: dict, high: float, low: float,
                    regime: str = "NEUTRAL", block_new: bool = False) -> list[Trade]:
        events: list[Trade] = []
        ind   = snapshot["indicators"]
        price = snapshot["price"]["current"]

        if self.cooldown > 0:
            self.cooldown -= 1

        # ── S_IDLE: scan for a strict short setup ─────────────────────────────
        if self.state == S_IDLE:
            if block_new or self.cooldown > 0:
                return events

            risk = SHORT_RISK_USD.get(regime, 0.0)
            if risk <= 0.0:                       # BULL (or unknown) → no shorts
                return events

            atr_val   = ind["atr_14"]
            rsi_val   = ind["rsi_14"]
            adx_val   = ind["adx"]
            macd_h    = ind["macd_histogram"]
            bb_pos    = ind["bb_position"]
            ema_align = ind["ema_alignment"]
            vol_ratio = snapshot["volume"]["volume_ratio"]

            setup = (
                ema_align == "BEARISH"
                and macd_h < 0
                and (SHORT_RSI_FLOOR < rsi_val < SHORT_RSI_MAX)
                and adx_val > SHORT_ADX_MIN
                and bb_pos < SHORT_BB_POS_MAX
                and vol_ratio > SHORT_VOL_MIN
                and atr_val > 0 and price > 0
            )
            if setup:
                sl_dist  = atr_val * SHORT_SL_ATR
                sl_frac  = sl_dist / price
                notional = risk / sl_frac if sl_frac > 0 else SHORT_MIN_NOTIONAL_USD
                notional = max(SHORT_MIN_NOTIONAL_USD, min(notional, SHORT_MAX_NOTIONAL_USD))

                self.state    = S_FULL
                self.entry    = price
                self.notional = notional
                self.atr      = atr_val
                self.sl       = price + sl_dist                      # above (short)
                self.tp1      = price - atr_val * SHORT_TP1_ATR      # below
                self.tp2      = price - atr_val * SHORT_TP2_ATR
                self.best     = price
                self.bars     = 0

                fee = notional * EXEC_COST_PER_SIDE
                events.append(Trade(symbol=self.symbol, direction="SHORT", kind="SHORT",
                                    entry=price, exit=0.0, pnl=-fee, exit_type="OPEN"))
            return events

        # ── S_FULL: manage full short until TP1 / SL / timeout ────────────────
        if self.state == S_FULL:
            self.bars += 1
            self.best  = min(self.best, low)     # best (lowest) for a short

            tp1_hit   = low  <= self.tp1
            sl_hit    = high >= self.sl
            timed_out = self.bars >= SHORT_TIMEOUT_BARS

            if tp1_hit:
                exit_p = self.tp1
                half   = self.notional * 0.50
                gross  = half * (exit_p - self.entry) / self.entry * (-1)   # short
                pnl    = gross - half * EXEC_COST_PER_SIDE
                events.append(Trade(symbol=self.symbol, direction="SHORT", kind="SHORT",
                                    entry=self.entry, exit=exit_p, pnl=pnl, exit_type="TP1"))
                self.state    = S_TRAIL
                self.notional = half
                self.sl       = self.entry            # SL → breakeven
                self.bars     = 0
            elif sl_hit:
                self._close(events, self.sl, "SL")
            elif timed_out:
                self._close(events, (high + low) / 2, "TIMEOUT")
            return events

        # ── S_TRAIL: remaining 50%, trailing SL + TP2 ─────────────────────────
        if self.state == S_TRAIL:
            self.bars += 1
            self.best  = min(self.best, low)
            trail_sl   = self.best + self.atr * SHORT_TRAIL_ATR    # above (short)

            if low <= self.tp2:
                self._close(events, self.tp2, "TP2")
            elif high >= trail_sl:
                self._close(events, trail_sl, "TRAIL")
            elif self.bars >= SHORT_TIMEOUT_BARS:
                self._close(events, (high + low) / 2, "TIMEOUT")
            return events

        return events

    def _close(self, events: list[Trade], exit_p: float, exit_type: str) -> None:
        gross = self.notional * (exit_p - self.entry) / self.entry * (-1)   # short
        pnl   = gross - self.notional * EXEC_COST_PER_SIDE
        events.append(Trade(symbol=self.symbol, direction="SHORT", kind="SHORT",
                            entry=self.entry, exit=exit_p, pnl=pnl, exit_type=exit_type))
        self.state    = S_IDLE
        self.cooldown = SHORT_COOLDOWN_BARS
