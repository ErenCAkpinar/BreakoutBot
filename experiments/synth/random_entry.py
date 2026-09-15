"""Research-only: random entries through the deployed exit geometry.

A pure test of the EXIT machinery and its accounting. The signal engine is
replaced by a coin flip (a STRONG_LONG with probability `p_entry` on any idle
bar), the probe always confirms, and the regime gate is lifted (BT_NO_REGIME).
Everything after the entry — probe fee, full-size fill at the confirm bar's
close, SL/TP1/TP2/trail/timeout, adverse fills, portfolio caps, drawdown
guards — is the untouched code path.

What it answers, on a price-martingale (`null_mart`): the geometry's expected
R must be −(entry + exit fees). A result above that is optimism in the exit
conventions or the accounting, not in the market — the entry cannot help,
because it knows nothing. On `trend`: whether the geometry alone (long-only,
wide stop, trail) harvests autocorrelation without any signal — the control
that decides if the entry signal adds anything over the exits.

Installed only inside the replay process by experiments/synth/run.py
(--random-entry P); strategy.py, config.py and paper_bb.py are unchanged.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from functools import partial

import numpy as np

import backtest
from strategy import SymbolState


class _CoinFlipEngine:
    """Stands in for MathEngine.analyze: no indicators, no score, a Bernoulli."""

    def __init__(self, rng: np.random.Generator, p: float) -> None:
        self._rng = rng
        self._p = p

    def analyze(self, snapshot: dict) -> dict:
        long = self._rng.random() < self._p
        return {"signal": "STRONG_LONG" if long else "NEUTRAL", "composite_score": 0.0}


@dataclass
class RandomEntryState(SymbolState):
    p_entry: float = 0.003     # ≈ the machine's own probe rate per idle bar
    seed:    int   = 0
    _rng: np.random.Generator = field(default=None, repr=False)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not 0.0 < self.p_entry < 1.0:
            raise ValueError("p_entry must be in (0, 1)")
        # str hashes are salted per process; crc32 keeps the stream reproducible.
        self._rng = np.random.default_rng(zlib.crc32(self.symbol.encode()) + 1_000_003 * self.seed)
        self.engine = _CoinFlipEngine(self._rng, self.p_entry)  # type: ignore[assignment]

    def _confirm(self, price: float, rsi_val: float, vol_ratio: float, mult: int) -> bool:
        return True


def install(p_entry: float, seed: int) -> None:
    """Register the variant for this replay process only."""
    RandomEntryState(symbol="VALIDATE", p_entry=p_entry, seed=seed)
    setattr(backtest, "SymbolState", partial(RandomEntryState, p_entry=p_entry, seed=seed))
    setattr(backtest, "_NO_REGIME", True)
