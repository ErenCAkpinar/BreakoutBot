"""Research-only TP2 partial exit, installed solely in the replay process.

The live strategy/config are unchanged. The residual keeps its original phase
deadline and portfolio slot; its TP2 becomes unreachable after the one partial.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from math import isfinite

import metrics
import strategy
from strategy import SymbolState, Trade, TRAILING


@dataclass
class TP2RunnerState(SymbolState):
    close_fraction: float = 0.5

    def __post_init__(self) -> None:
        if not isfinite(self.close_fraction) or not 0 <= self.close_fraction <= 1:
            raise ValueError("TP2 close fraction must be finite and in [0, 1]")
        if not strategy.ADVERSE_FILLS:
            raise ValueError("TP2 runner requires adverse fills")
        if strategy.TP1_CLOSE_FRAC != 0:
            raise ValueError("TP2 runner requires TP1_CLOSE_FRAC=0")

    def process_bar(self, snapshot: dict, high: float, low: float,
                    rsi_val: float, vol_ratio: float,
                    block_new_full: bool = False,
                    size_mult: float = 1.0) -> list[Trade]:
        direction = 1 if self.direction == "LONG" else -1
        if self.state == TRAILING and self.close_fraction == 0:
            self.full_tp2 = direction * float("inf")

        trail_sl = self.trail_best - direction * self.test_atr * strategy.TRAIL_ATR
        target_hit = high >= self.full_tp2 if direction == 1 else low <= self.full_tp2
        trail_hit = low <= trail_sl if direction == 1 else high >= trail_sl
        partial_hit = (self.state == TRAILING and 0 < self.close_fraction < 1
                       and target_hit and not trail_hit)
        if not partial_hit:
            return super().process_bar(snapshot, high, low, rsi_val, vol_ratio,
                                       block_new_full=block_new_full,
                                       size_mult=size_mult)

        # The original, completed-bar trail has survived. TP2 is reached on this
        # bar, so realize only the chosen fraction, charging its one exit fee.
        if self.cooldown > 0:
            self.cooldown -= 1
        self.bars_held += 1
        part = self.full_notional * self.close_fraction
        gross = part * (self.full_tp2 - self.full_entry) / self.full_entry * direction
        event = Trade(self.symbol, self.direction, "FULL", self.full_entry,
                      self.full_tp2, gross - part * strategy.EXEC_COST_PER_SIDE,
                      "TP2_PARTIAL")
        events = [event]
        self.trades.append(event)
        self.full_notional -= part
        # A new full entry restores its finite target in the unchanged base code.
        self.full_tp2 = direction * float("inf")

        if self.bars_held >= strategy.TIMEOUT_BARS:
            # TP2 does not buy the residual an extra bar or a fresh time budget.
            exit_price = snapshot["price"]["current"]
            gross = (self.full_notional * (exit_price - self.full_entry)
                     / self.full_entry * direction)
            event = Trade(self.symbol, self.direction, "FULL", self.full_entry,
                          exit_price,
                          gross - self.full_notional * strategy.EXEC_COST_PER_SIDE,
                          "TIMEOUT")
            events.append(event)
            self.trades.append(event)
            self._reset(cooldown=strategy.COOLDOWN_BARS)
        else:
            # Ratchet only AFTER the completed bar; never apply this newly raised
            # level to a low/high that may have occurred before the TP2 fill.
            self.trail_best = (max(self.trail_best, high) if direction == 1
                               else min(self.trail_best, low))
        return events


def install_runner(close_fraction: float) -> None:
    """Register the variant for this standalone backtest process only."""
    TP2RunnerState(symbol="VALIDATE", close_fraction=close_fraction)
    metrics.PARTIAL_EXITS.add("TP2_PARTIAL")
    setattr(strategy, "SymbolState", partial(TP2RunnerState,
                                             close_fraction=close_fraction))
