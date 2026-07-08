"""
BreakoutBot — Configuration
Wave 11 signal engine + Test-Confirm-Scale execution layer
"""

# ── Token universe (23 tokens — curated after 30d backtest) ──────────────────
#
# Wave 12 (post-backtest) cull — 10 removed for structural failure:
#   ATOMUSDT    WR 28.6%, PF 0.25  → terrible WR, mean-reverting (β=0.92)
#   1000PEPEUSDT WR 33.3%, PF 0.44 → bad WR, high volatility noise
#   1000BONKUSDT WR 50.0%, PF 0.59 → bad R:R, too choppy
#   POPCATUSDT  WR 28.6%, PF 0.20  → worst PF in universe
#   RENDERUSDT  WR 53.3%, PF 0.61  → insufficient trend quality
#   EIGENUSDT   WR 53.8%, PF 0.57  → excessive SL rate (12/26 trades)
#   OPUSDT      WR 50.0%, PF 0.45  → bad WR + bad R:R
#   ONDOUSDT    WR 60.0%, PF 0.72  → choppy (30 trades, TP2 hit only 1×)
#   ENAUSDT     WR 55.6%, PF 0.71  → marginal R:R
#   STRKUSDT    WR 74.1%, PF 0.87  → high WR but avg win ≈ 0.3× avg loss
#
# ── Full 23-coin universe (FAZ 4 validation, 2026-06) ─────────────────────────
# Validated on 90d + 240d with Faz 4c (BULL-only longs + MR). In the current
# bear/chop regime only 8 of 23 hold edge; the rest bleed (BTC PF 0.19, WIF/STX/
# AAVE/JUP/PENDLE/SUI/APT structural losers; FET bear-fragile −13%/DD−15%).
# Re-validate + re-expand toward this when BTC regime turns BULL. See BENCHMARKS.md.
_FULL_UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "NEARUSDT", "XRPUSDT", "ADAUSDT",
    "LINKUSDT", "UNIUSDT", "LTCUSDT", "POLUSDT", "APTUSDT", "ARBUSDT", "INJUSDT",
    "SUIUSDT", "WIFUSDT", "JUPUSDT", "AAVEUSDT", "FETUSDT", "LDOUSDT", "PENDLEUSDT",
    "STXUSDT", "ORDIUSDT",
]

# ── ACTIVE universe — curated 8 (FAZ 4c) ──────────────────────────────────────
# Kept: positive on 90d (chop) AND controlled (DD<10%, no blow-up) on 240d bear.
#   90d (8 coins, Faz 4c): 8/8 profitable, PF 3.29, MaxDD −3.25%, +$55.51/mo
#   240d deep bear:        −$16.42/mo, MaxDD −9.66% (defended, no hard stop)
TOKENS = [
    "INJUSDT", "POLUSDT", "LDOUSDT",      # robust: positive in BOTH 90d & 240d
    "SOLUSDT", "AVAXUSDT", "NEARUSDT",    # 90d-positive, bear-controlled
    "UNIUSDT", "ADAUSDT",
]
# Total: 8 curated tokens (of 23 — re-expand when market regime turns)

# ── Timeframe ─────────────────────────────────────────────────────────────────
TIMEFRAME  = "5m"
BARS_PER_DAY = 288   # 5m bars in 24 h

# ── Signal thresholds (Wave 11 + ~7% loosening) ───────────────────────────────
#
#  Wave 11 originals:    STRONG_LONG > 70, RSI_OVERBOUGHT_LONG = 68, BB_CAP = 0.95
#  Loosened (~7%):       STRONG_LONG > 67, RSI_OVERBOUGHT_LONG = 70, BB_CAP = 0.97
#
STRONG_LONG_THRESHOLD  = 67.0   # was 70 in Wave 11 — ~3 pts looser
STRONG_SHORT_THRESHOLD = 33.0   # was 30 in Wave 11
WEAK_LONG_THRESHOLD    = 55.0   # unchanged
WEAK_SHORT_THRESHOLD   = 45.0   # unchanged

RSI_OVERBOUGHT_LONG    = 70.0   # was 68 — 2 pts looser (still blocks RSI > 70)
RSI_OVERSOLD_SHORT     = 30.0   # was 32 — symmetric
BB_UPPER_CAP_LONG      = 0.97   # was 0.95 — only blocks extreme top (top 3%)
BB_LOWER_CAP_SHORT     = 0.03   # was 0.05 — symmetric

HURST_LONG_MIN         = 0.58   # raised 0.55→0.58 — stricter trending regime requirement for LONG

# ── BTC macro gate ────────────────────────────────────────────────────────────
# Gate LONG signals on ALL tokens when BTC is in a strong downtrend.
# Rationale: most altcoins have high BTC correlation — fighting BTC macro = losses.
BTC_GATE_ENABLED  = True    # master switch
BTC_GATE_RETURN   = -0.015  # block new LONGs when BTC 4h return < -1.5%

# ── Beta scoring ──────────────────────────────────────────────────────────────
# beta = cov(token_ret, btc_ret) / var(btc_ret) over rolling window.
# beta > 1: token amplifies BTC moves (ideal for breakout longs in BTC uptrend).
# beta < 0: token moves against BTC (rare, use with caution).
BTC_BETA_WINDOW   = 48      # bars for beta calc (48 × 5m = 4 hours)
BTC_BETA_BOOST    = 2.0     # beta > 2 + BTC uptrend → loosen STRONG threshold by 1pt
BTC_BETA_BLOCK    = 3.0     # beta > 3 + BTC downtrend → extra block (avoid high-beta in crash)

# ── Execution parameters ──────────────────────────────────────────────────────
TEST_SIZE_USD   = 20.0    # $20 test position (flat, not % of balance)
FULL_SIZE_USD   = 300.0   # legacy flat size (fallback only — see RISK_PER_TRADE_USD)
LEVERAGE        = 3       # 3× leverage cap on full position

# ── Risk-based position sizing (replaces flat $900 notional) ──────────────────
# Size each full position so the SL costs a FIXED dollar amount regardless of the
# coin's volatility. Flat sizing made volatile coins (FET) risk ~$14 while calm
# coins (NEAR) risked ~$9 on the SAME 1.5×ATR stop. Now: notional = RISK_$ / sl_frac.
RISK_PER_TRADE_USD = 10.0    # ≈1% of $1000 — every full SL costs ~this, any coin
MIN_NOTIONAL_USD   = 300.0   # floor
MAX_NOTIONAL_USD   = 1500.0  # ceiling (bounds leverage on a ~$1000 account)
MAX_OPEN        = 2       # max simultaneous FULL positions across all symbols

# ── Exit parameters ───────────────────────────────────────────────────────────
SL_TEST_ATR     = 1.0     # test SL = 1×ATR  (small loss if signal fails)
SL_FULL_ATR     = 1.5     # full SL = 1.5×ATR from entry
TP1_ATR         = 2.0     # TP1 = entry ± 2×ATR  → close 50%, move SL to breakeven
TP2_ATR         = 4.0     # TP2 = entry ± 4×ATR  → close remaining 50%
TRAIL_ATR       = 1.5     # trailing SL = peak_price ∓ 1.5×ATR (active after TP1)
TIMEOUT_BARS    = 48      # 48 bars = 4 hours max hold on full position

# ── Confirmation check (1 bar after test entry) ───────────────────────────────
CONFIRM_PRICE_MOVE_PCT = 0.0005  # price must move ≥ 0.05% in signal direction (tiny move, just anti-reversal)
CONFIRM_VOL_MULT       = 1.0     # volume ≥ 1.0× avg (normalizes after signal bar — Wave 11 signal already vets vol)
CONFIRM_RSI_LONG_MIN   = 45.0    # RSI still ≥ 45 for LONG (was 50 — 1-bar dips below 50 common after breakout)
CONFIRM_RSI_SHORT_MAX  = 55.0    # RSI still ≤ 55 for SHORT (symmetric)

# ── Cooldown ──────────────────────────────────────────────────────────────────
COOLDOWN_BARS = 10        # bars to wait after failed signal before re-scanning

# ── FAZ 2: Range Mean-Reversion sleeve ────────────────────────────────────────
# A SECOND, uncorrelated strategy running in parallel to momentum-breakout. Where
# momentum BUYS breakouts, MR FADES oversold dips back to the mean — but ONLY in a
# ranging market (regime == NEUTRAL + low ADX), where breakouts fail and reversion
# works. Long-only in Faz 2 (MR-short waits for Faz 3). Gated to NEUTRAL only
# (closed in BULL = trending up, BEAR = falling-knife risk) per §9 matrix.
MR_ENABLED            = True
MR_RISK_PER_TRADE_USD = 5.0     # half the momentum $10 (smaller edge, §9)
MR_MIN_NOTIONAL_USD   = 150.0   # half momentum floor
MR_MAX_NOTIONAL_USD   = 750.0   # half momentum ceiling
MR_ADX_MAX            = 20.0    # only when ADX < 20 (no trend = range)
MR_RSI_OVERSOLD       = 32.0    # buy when RSI < this (oversold)
MR_BB_POS_MAX         = 0.12    # AND price in bottom 12% of Bollinger band
MR_SL_ATR             = 1.0     # stop = 1×ATR below entry
MR_TIMEOUT_BARS       = 24      # exit if no reversion in 24 bars (~2h on 5m)
MR_COOLDOWN_BARS      = 6       # wait after an MR trade closes

# ── FAZ 3: Short sleeve (separate strict price-action shorts) ─────────────────
# The Wave-11 MathEngine is long-biased → produces ~0 STRONG_SHORT signals
# (validated: 0 shorts / 131 trades over 90d). Research REJECTED loosening the 33
# threshold (adds weak signals to the unbounded-loss side). So shorts get their
# OWN strict price-action engine: breakdown + bearish structure + volume
# conviction (an OI-rise proxy until live OI is wired). Regime-gated per §9:
#   BEAR    → emphasized, MEDIUM $8 risk
#   NEUTRAL → CLOSED  ← was $5, but backtest proved NEUTRAL shorts BLEED
#             (sideways chop squeezes shorts: 112-239 over-fires, PF<1, -$100/coin).
#             Data overrides the §9 "neutral small short" idea — shorts are
#             BEAR-only insurance. Re-test before ever re-enabling NEUTRAL.
#   BULL    → CLOSED (never short an uptrend)
# DISABLED — shorts tested thoroughly, found NO EDGE in this system:
#   90d (neutral):  −$19/mo vs Faz 2 (over-fires in chop, gets squeezed)
#   240d (real bear, 35-49% BEAR bars): STILL net −$158/3coins, WR 42-57%, PF<1
#   Root cause: crypto bears have violent short-squeeze rallies; a breakdown-
#   momentum short (the symmetric mirror of the long edge) gets stopped out on
#   the bounces. Profitable shorting needs DIFFERENT logic (failed-rally fades,
#   funding/liquidation signals) — not breakdown momentum. Code kept for a
#   future redesign + dedicated study. Deployable system = Faz 2 (long + MR).
SHORT_ENABLED          = False
SHORT_RISK_USD         = {"BEAR": 8.0, "NEUTRAL": 0.0, "BULL": 0.0}
SHORT_MIN_NOTIONAL_USD = 150.0
SHORT_MAX_NOTIONAL_USD = 1200.0
SHORT_ADX_MIN          = 26.0    # only short a STRONG trend (was 20 — too loose)
SHORT_RSI_MAX          = 42.0    # committed bearish momentum (was 48)
SHORT_RSI_FLOOR        = 22.0    # but not so oversold it snaps back
SHORT_BB_POS_MAX       = 0.22    # deeper breakdown required (was 0.35)
SHORT_VOL_MIN          = 1.4     # real conviction / OI-rise proxy (was 1.1)
SHORT_SL_ATR           = 1.5
SHORT_TP1_ATR          = 2.0
SHORT_TP2_ATR          = 4.0
SHORT_TRAIL_ATR        = 2.5     # wider trail — don't get squeezed out (was 1.5)
SHORT_TIMEOUT_BARS     = 48
SHORT_COOLDOWN_BARS    = 24      # less re-firing into the same move (was 10)

# ── Fees + slippage (real, measured 2026-05-30) ──────────────────────────────
# Fee: Binance USDⓈ-M Futures regular-user (VIP 0) taker = 0.05% (web-verified).
#      Strategy uses MARKET orders both sides → taker applies. BNB-pay → 0.045%.
# Slippage: measured live from order books for all 23 config coins, $900 notional.
#      Per-side: median 0.015%, mean 0.016%, max 0.048% (ARB). We use a
#      conservative 0.025% (75th pct) — covers the thinner alts the bot trades.
TAKER_FEE          = 0.0005     # 0.05% per side (real Binance taker)
SLIPPAGE_PCT       = 0.00025    # 0.025% per side (conservative, live-measured)
EXEC_COST_PER_SIDE = TAKER_FEE + SLIPPAGE_PCT   # 0.075% all-in per side

# ── Risk management (Wave 11 Phase A DD circuit breakers) ────────────────────
DAILY_DD_LIMIT     = -0.05  # -5% intraday → freeze new entries (loose: 8-coin book)
EQUITY_THROTTLE_DD = -0.07  # -7% from equity peak → halve position sizing (throttle)
PEAK_DD_LIMIT      = -0.15  # -15% from equity peak → hard stop (pause strategy)
DAILY_SL_LIMIT   = 2      # per-symbol: after 2 SL hits in a day → freeze that symbol for the day

# ── Capital ───────────────────────────────────────────────────────────────────
INITIAL_BALANCE  = 1000.0

# ── Session filter (same as Wave 11) ─────────────────────────────────────────
SESSION_START_UTC = 4
SESSION_END_UTC   = 23
