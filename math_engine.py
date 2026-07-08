"""
MathEngine — Wave 11 signal scoring engine with ~7% loosened thresholds.

Copied from CryptoSentinelTrader ai_engine/decision_engine.py (MathEngine class).
Standalone — no external imports required.

Wave 11 → BreakoutBot changes (loosening ~7%):
  STRONG_LONG threshold  : 70 → 67   (more signals)
  RSI_OVERBOUGHT_LONG    : 68 → 70   (less aggressive blocking)
  BB_UPPER_CAP_LONG      : 0.95 → 0.97  (only extreme stretches blocked)
  Everything else        : UNCHANGED
"""

from __future__ import annotations


class MathEngine:
    """Pure mathematical analysis — runs on EVERY snapshot, zero cost, <10ms.

    Scoring architecture (Wave 9, fixed weights — regime weighting reverted):
        composite = trend×0.30 + momentum×0.25 + volume×0.20
                  + volatility×0.10 + structure×0.15

    Hard caps (Wave 9.1 + 9.3, LOOSENED ~7% for BreakoutBot):
        RSI > 70 → cap composite at STRONG threshold (blocks entry)
        BB pos > 0.97 → same
    """

    # ── Signal thresholds (BreakoutBot loosened) ──────────────────────────────
    STRONG_LONG_THRESHOLD  = 67.0   # Wave 11 was 70
    STRONG_SHORT_THRESHOLD = 33.0   # Wave 11 was 30
    WEAK_LONG_THRESHOLD    = 55.0   # unchanged
    WEAK_SHORT_THRESHOLD   = 45.0   # unchanged

    # ── Hard caps (BreakoutBot loosened) ─────────────────────────────────────
    RSI_OVERBOUGHT_LONG  = 70.0    # Wave 11 was 68
    RSI_OVERSOLD_SHORT   = 30.0    # Wave 11 was 32
    BB_UPPER_CAP_LONG    = 0.97    # Wave 11 was 0.95
    BB_LOWER_CAP_SHORT   = 0.03    # Wave 11 was 0.05

    def analyze(self, snapshot: dict) -> dict:
        """Score the market state using technical indicators.
        Returns dict with per-indicator scores, composite score, signal string.
        """
        indicators = snapshot.get("indicators", {})
        price      = snapshot.get("price", {})
        volume     = snapshot.get("volume", {})

        trend_score     = self._score_trend(price, indicators)
        momentum_score  = self._score_momentum(indicators)
        volume_score    = self._score_volume(volume)
        volatility_score= self._score_volatility(indicators, price)
        structure_score = self._score_market_structure(indicators, snapshot)

        # Fixed weights (Wave 9.4 regime weighting reverted — overfit on Wave 11)
        composite = (
            trend_score      * 0.30
            + momentum_score * 0.25
            + volume_score   * 0.20
            + volatility_score * 0.10
            + structure_score  * 0.15
        )

        # Hard caps before signal determination (Wave 9.1 + 9.3)
        rsi    = indicators.get("rsi_14", 50.0)
        bb_pos = indicators.get("bb_position", 0.5)
        composite, cap_reason = self._apply_signal_caps(composite, rsi, bb_pos)

        # Signal determination
        if composite >= self.STRONG_LONG_THRESHOLD:
            signal = "STRONG_LONG"
        elif composite >= self.WEAK_LONG_THRESHOLD:
            signal = "WEAK_LONG"
        elif composite <= self.STRONG_SHORT_THRESHOLD:
            signal = "STRONG_SHORT"
        elif composite <= self.WEAK_SHORT_THRESHOLD:
            signal = "WEAK_SHORT"
        else:
            signal = "NEUTRAL"

        return {
            "composite_score":   round(composite, 1),
            "trend_score":       round(trend_score, 1),
            "momentum_score":    round(momentum_score, 1),
            "volume_score":      round(volume_score, 1),
            "volatility_score":  round(volatility_score, 1),
            "structure_score":   round(structure_score, 1),
            "signal":            signal,
            "signal_cap_reason": cap_reason,
        }

    # ── Hard caps ─────────────────────────────────────────────────────────────

    def _apply_signal_caps(self, composite: float, rsi: float, bb_pos: float) -> tuple:
        """Apply hard caps for extreme overbought/oversold entries.
        Returns (capped_composite, reason_or_None).
        """
        # LONG-side caps
        if composite >= self.STRONG_LONG_THRESHOLD:
            if rsi > self.RSI_OVERBOUGHT_LONG:
                return self.STRONG_LONG_THRESHOLD - 0.1, "RSI_OVERBOUGHT"
            if bb_pos > self.BB_UPPER_CAP_LONG:
                return self.STRONG_LONG_THRESHOLD - 0.1, "BB_UPPER"
        # SHORT-side caps
        if composite <= self.STRONG_SHORT_THRESHOLD:
            if rsi < self.RSI_OVERSOLD_SHORT:
                return self.STRONG_SHORT_THRESHOLD + 0.1, "RSI_OVERSOLD"
            if bb_pos < self.BB_LOWER_CAP_SHORT:
                return self.STRONG_SHORT_THRESHOLD + 0.1, "BB_LOWER"
        return composite, None

    # ── Scoring components (unchanged from Wave 11) ───────────────────────────

    def _score_trend(self, price: dict, indicators: dict) -> float:
        score    = 50.0
        trend    = price.get("trend", "SIDEWAYS")
        strength = price.get("trend_strength", 0.0)

        if trend == "UP":
            score = 50.0 + min(strength * 50.0, 40.0)
        elif trend == "DOWN":
            score = 50.0 - min(strength * 50.0, 40.0)

        alignment = indicators.get("ema_alignment", "MIXED")
        if alignment == "BULLISH":
            score = min(score + 10.0, 100.0)
        elif alignment == "BEARISH":
            score = max(score - 10.0, 0.0)
        return score

    def _score_momentum(self, indicators: dict) -> float:
        rsi_val   = indicators.get("rsi_14", 50.0)
        macd_hist = indicators.get("macd_histogram", 0.0)

        if 40 <= rsi_val <= 60:
            rsi_score = 50.0
        elif 30 <= rsi_val < 40:
            rsi_score = 65.0     # Oversold → buying opportunity
        elif rsi_val < 30:
            rsi_score = 80.0     # Strongly oversold
        elif 60 < rsi_val <= 70:
            rsi_score = 40.0     # Approaching overbought
        else:
            rsi_score = 20.0     # Overbought → danger

        if macd_hist > 0:
            rsi_score = min(rsi_score + 5.0, 100.0)
        elif macd_hist < 0:
            rsi_score = max(rsi_score - 5.0, 0.0)
        return rsi_score

    def _score_volume(self, volume: dict) -> float:
        vol_ratio = volume.get("volume_ratio", 1.0)
        buy_sell  = volume.get("buy_sell_ratio", 0.5)

        if vol_ratio > 1.5 and buy_sell > 0.6:
            return 75.0   # High volume, buyers dominant → bullish
        if vol_ratio > 1.5 and buy_sell < 0.4:
            return 25.0   # High volume, sellers dominant → bearish
        if vol_ratio > 2.0:
            return 60.0   # Volume spike without clear direction
        if vol_ratio < 0.5:
            return 40.0   # Low volume → weak signal
        return 50.0

    def _score_volatility(self, indicators: dict, price: dict) -> float:
        atr_val = indicators.get("atr_14", 0.0)
        current = price.get("current", 1.0)
        atr_pct = (atr_val / current * 100) if current > 0 else 0.0
        return max(0.0, min(100.0, 100.0 - atr_pct * 15.0))

    def _score_market_structure(self, indicators: dict, snapshot: dict) -> float:
        """ADX strength + VWAP position."""
        adx_val   = indicators.get("adx", 0.0)
        vwap_dist = indicators.get("vwap_distance_pct", 0.0)
        trend     = snapshot.get("price", {}).get("trend", "SIDEWAYS")

        adx_score = 70.0 if adx_val > 30 else 55.0 if adx_val > 20 else 40.0

        if trend == "UP" and vwap_dist > 0:
            vwap_score = 65.0
        elif trend == "DOWN" and vwap_dist < 0:
            vwap_score = 35.0
        else:
            vwap_score = 50.0

        return (adx_score + vwap_score) / 2
