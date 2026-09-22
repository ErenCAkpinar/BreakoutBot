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

# ── ACTIVE universe — 4 (2026-09-22: POL removed from the curated 5) ──────────
# The previous 8 were curated 2026-07-29 under the OLD exit structure (trail
# 1.5×ATR + 50% partial at TP1). The bot now runs E6 (X_TRAIL_ATR=2.5,
# X_TP1_CLOSE_FRAC=0.0), and a trend-following exit ranks coins differently than a
# scale-out one — so the universe was re-ranked under the params actually deployed.
#
# Dropped — negative expectancy under E6 in every window that has a sample:
#   LDOUSDT   live −0.553R (n=13, p=0.005) · fresh90 −0.510R (last of 23, DD −12.4%)
#             · fresh240 −0.145R. Alone cost −$71.88 live = ~2× the sleeve's total
#             net loss; without it the momentum sleeve was +$33.
#   SOLUSDT   live −0.726R (n=4) · fresh90 −0.292R · fresh240 −0.035R · pinned240
#             −0.149R — negative in 4/4 windows.
#   AVAXUSDT  live −0.582R (n=3) · fresh90 −0.426R · fresh240 −0.178R · pinned240
#             −0.156R — negative in 4/4 windows.
#
# Pooled on the 240d window: +0.131R → +0.249R per position (245 → 168 positions).
#
# NOT dropped despite a weak live run: ADA (live −0.141R but p=0.36 → noise; +0.252R
# on 240d, n=26) and NEAR (live n=1; +0.170R on 240d with n=48, the largest sample
# in the set). Cutting those would have been fitting noise.
# 2026-09-22 — POLUSDT removed, by owner decision AGAINST the selection rule below.
# Live under the Faz 8 defaults (27 Aug → 22 Sep): n=11, WR 9%, −0.52R/pos, −$57
# (t=−2.8 in isolation; one of 5 coins, picked post hoc as the worst). The same
# geometry backtested ranks POL SECOND of the five in BOTH long windows —
# 240d +0.51R (n=21), 665d +0.25R (n=56) — while ADA (kept) is last on 665d at
# −0.09R. The 90d-vs-live rank correlation of −0.55 measured below is exactly this
# pattern, and DEFTER Tur 9 later filed the 2026-08-22 cull as "a trace of the
# selection, not an edge estimate". So this is recorded as what it is: a cut on
# 11 live positions, not a finding. No R3-noPOL arm was run; the direction is
# known (removing the #2 coin loses in both windows). If POL is ever re-added,
# do it via experiments/ on 240d AND 665d, not on a live month.
TOKENS = [
    "UNIUSDT", "INJUSDT", "ADAUSDT",      # +0.34 / +0.30 / +0.25 R on 240d (E6)
    "NEARUSDT",                           # +0.17 R on 240d (E6)
]
# Total: 4 tokens (of 23). POLUSDT: +0.18R on 240d (E6), removed 2026-09-22 (above).
#
# ⚠️ Selection rule — do NOT curate on a short window. Measured 2026-08-22, per-coin
# expectancy rank correlation: 90d vs 240d = −0.31, 90d vs live = −0.55 (sign
# agreement 1/8), 240d vs live = +0.74. A 90d window has ~5–20 positions per coin and
# anti-predicts the next period; only the 240d window (25–48/coin) transfers. Every
# candidate that screened well on 90d flipped negative on 240d — FET +0.240→−0.073,
# XRP +0.138→−0.144, AAVE +0.119→−0.045, WIF +0.031→−0.154. LINKUSDT (+0.106/+0.091)
# and LTCUSDT (+0.079/+0.086) are the only two positive in both, but at n=19/15 that
# is indistinguishable from zero — held back until the account is off the −15% guard.

# ── Timeframe ─────────────────────────────────────────────────────────────────
TIMEFRAME  = "5m"
BARS_PER_DAY = 288   # 5m bars in 24 h

# ── Regime warm-up (M1) ───────────────────────────────────────────────────────
# regime.score_series_4h needs MA_PERIOD(200) + SLOPE_LOOKBACK(10) = 210 CLOSED 4h
# bars before it can label a bar; until then regime.py sets score = 0.0 → NEUTRAL,
# and LONG_SIZE_MULT["NEUTRAL"] = 0.0 means momentum trades NOTHING. 210 × 4h = 35d.
# A backtest whose 5m frame starts at the trading window therefore fabricates a
# flat month at the head of EVERY window (and hands it to the MR sleeve, which is
# gated to NEUTRAL). Callers must fetch this many extra days BEFORE the window and
# pass `trade_start_idx` to backtest.run_symbol.
REGIME_WARMUP_DAYS = 40                              # 35d required + margin
REGIME_WARMUP_BARS = REGIME_WARMUP_DAYS * BARS_PER_DAY

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
# Env-overridable for controlled A/B sweeps (same pattern as regime.LONG_SIZE_MULT).
# Never hand-tune these on the live bot; run an arm through experiments/run_arm.sh
# and let experiments/ledger.py rule on it first.
#
# ── ADOPTED 2026-08-27: arm R1-sl225t96 (experiments/DEFTER.md, Tur 1) ────────
# Passed the two-window rule — the ONLY rule that adopts anything here:
#
#            240d              665d (BT_RESTARTS=1, full window both arms)
#   before   $1370.04          $731.93   ·  7 hard stops · MaxDD -57.3%
#   after    $1435.46 (+$65)   $1085.22  ·  3 hard stops · MaxDD -37.6%   (+$353)
#
# What is ACTUALLY established, and what is not:
#   · CERTAIN (arithmetic): entry fees fall because risk is a fixed DOLLAR amount,
#     so a wider stop buys a SMALLER notional for the same risk. -$366 → -$223 on
#     665d, -$147 → -$85 on 240d. This cannot regress out of sample.
#   · CERTAIN (measured): hard stops 7 → 3, MaxDD -57.3% → -37.6%. Fewer trips
#     into the risk limit means less throttling and fewer forced restarts.
#   · NOT ESTABLISHED: the per-position edge. Δ+0.063R against SE 0.085 is inside
#     the noise (t≈0.74) even at n≈400. The arm is adopted for its cost and
#     drawdown behaviour, NOT on a claim that it picks better trades.
#
# The time budget is not an independent lever: the control arm (SL 1.5 + 96 bars)
# gained $6.84 and left the exit mix untouched. TIMEOUT_BARS only matters BECAUSE
# the stop is wider — at SL 1.5 positions resolved long before 48 bars. The two
# move together or not at all.
#
# DEPLOY: the systemd unit used to carry `X_TRAIL_ATR=2.5` from E6, which would
#    have overridden the adopted 3.75. Verified 2026-09-22: both Environment lines
#    are commented out in the unit and DEPLOYED_SHA is dded71b (2026-08-27) — the
#    server runs these defaults. Keep it that way: no X_* exit override in the unit.
import os as _os
def _envf(name: str, default: float) -> float:
    return float(_os.getenv(name, default))

SL_TEST_ATR     = _envf("X_SL_TEST_ATR", 1.0)    # test SL = 1×ATR (small loss if signal fails)
SL_FULL_ATR     = _envf("X_SL_FULL_ATR", 2.25)   # was 1.5 — adopted 2026-08-27
TP1_ATR         = _envf("X_TP1_ATR", 3.0)        # was 2.0 — R geometry held constant
TP2_ATR         = _envf("X_TP2_ATR", 6.0)        # was 4.0
TRAIL_ATR       = _envf("X_TRAIL_ATR", 3.75)     # was 1.5 (E6 ran 2.5 via env)
TIMEOUT_BARS    = int(_envf("X_TIMEOUT_BARS", 96))   # was 48 — wider targets need the time

# Fraction of the position closed at TP1. Was hardcoded 0.50 in strategy.py, then
# 0.0 via env (E6) from 2026-08-08. Adopted as the default 2026-08-27: the
# scale-out caps winners near 0.7R while a stop-out still costs a full 1R.
TP1_CLOSE_FRAC  = _envf("X_TP1_CLOSE_FRAC", 0.0)

# ── Fill convention (E7) ─────────────────────────────────────────────────────
# OHLC cannot order touches WITHIN a bar. When one 5m bar touches both an
# adverse level (SL / trail) and a favorable one (TP1 / TP2), the sim must
# pick which filled first. The old code always picked the favorable fill, and
# the TRAILING branch even raised the trail from the CURRENT bar's high before
# testing that same bar's low against it — an exit that requires the high to
# happen before the low. Under EXIT-E6 nearly every position ends in TRAIL, so
# that optimism priced most of the deployed system's PnL (one reason every
# live period has underperformed its backtest).
#   default (1) : ambiguous fills resolve AGAINST the position; the trail only
#                 ratchets on completed bars; timeouts fill at the bar CLOSE
#                 (the only price a close-of-bar decision can actually get).
#                 The backtest becomes a floor, not a ceiling.
#   X_ADVERSE_FILLS=0 : reproduces the old optimistic convention — ONLY for
#                 comparing against numbers published before 2026-08-27.
#                 Never make a deploy decision on it.
ADVERSE_FILLS = _os.getenv("X_ADVERSE_FILLS", "1") != "0"

# ── Minimum stop distance (Faz 7 friction floor) ──────────────────────────────
# Round-trip cost as a fraction of the risk taken is
#       cost/risk = (notional × 2×EXEC_COST_PER_SIDE) / (notional × sl_frac)
#                 = 0.0015 / sl_frac
# The notional cancels: position SIZE cannot change this ratio, only the stop's
# distance from price can. Measured on the live August book, mean sl_frac was
# 0.861% → every position started 0.174R behind, against a measured net edge of
# +0.32R. The tightest setups were far worse (0.32% → 46% of risk paid in fees).
#
# This filter refuses a setup whose FULL stop would sit closer than this fraction
# of price, before the $20 probe is even paid for. 0.0 = off (no filter).
#   0.0075 → cost ≤ 20% of risk        0.0100 → cost ≤ 15%        0.0150 → ≤ 10%
# Default off: the live evidence was ambiguous (clamp-bound trades were −$0.77/pos
# vs −$1.07 for the rest, n=11 — the tight-stop trades were not the worse group),
# so this must earn its place on two windows like everything else.
MIN_SL_FRAC = _envf("X_MIN_SL_FRAC", 0.0)

# ── Confirmation check (1 bar after test entry) ───────────────────────────────
CONFIRM_PRICE_MOVE_PCT = _envf("X_CONFIRM_PRICE_MOVE_PCT", 0.0005)  # ≥0.05% move in signal direction
CONFIRM_VOL_MULT       = _envf("X_CONFIRM_VOL_MULT", 1.0)   # volume ≥ N× avg — 1.0 is effectively no filter
CONFIRM_RSI_LONG_MIN   = _envf("X_CONFIRM_RSI_LONG_MIN", 45.0)   # RSI still ≥ 45 for LONG
CONFIRM_RSI_SHORT_MAX  = _envf("X_CONFIRM_RSI_SHORT_MAX", 55.0)  # RSI still ≤ 55 for SHORT

# ── Cooldown ──────────────────────────────────────────────────────────────────
COOLDOWN_BARS = 10        # bars to wait after failed signal before re-scanning

# ── FAZ 2: Range Mean-Reversion sleeve ────────────────────────────────────────
# A SECOND, uncorrelated strategy running in parallel to momentum-breakout. Where
# momentum BUYS breakouts, MR FADES oversold dips back to the mean — but ONLY in a
# ranging market (regime == NEUTRAL + low ADX), where breakouts fail and reversion
# works. Long-only in Faz 2 (MR-short waits for Faz 3). Gated to NEUTRAL only
# (closed in BULL = trending up, BEAR = falling-knife risk) per §9 matrix.
# ── DISABLED 2026-08-27 (arm R1-mr-off, passed the two-window rule) ──────────
# Every measurement of this sleeve is negative once it is charged its own entry
# fees, across five independent samples:
#     live (n=13)              -0.228R
#     backtest, that month     -0.256R  (n=10)
#     240d backtest            -0.044R  (n=49)
#     665d backtest            -0.150R  (n=418 pooled book)
# Turning it off: 240d +$10.89 (PF 1.83→1.90, MaxDD -6.31%→-5.78%), 665d +$16.49.
#
# It was validated back in Faz 2 on SOL/INJ/FET under the OLD exit structure and
# never re-validated after either the exit change (E6) or the 8→5 re-curation —
# `paper_bb` gives both sleeves the SAME self.tokens, so Faz 6 silently re-scoped
# MR without measuring it. TODOS E1 asked for a re-curation; the answer turned out
# to be simpler than that.
#
# X_MR_ENABLED=1 turns it back on for a research run.
MR_ENABLED            = _os.getenv("X_MR_ENABLED", "0") != "0"
MR_RISK_PER_TRADE_USD = 5.0     # half the momentum $10 (smaller edge, §9)
MR_MIN_NOTIONAL_USD   = 150.0   # half momentum floor
MR_MAX_NOTIONAL_USD   = 750.0   # half momentum ceiling

# ── Per-sleeve risk (M2) ──────────────────────────────────────────────────────
# An R-multiple is pnl / the risk that position actually took. Momentum risks $10,
# MR risks $5. Scoring a pooled momentum+MR set at a single $10 denominator makes
# "expR" dollars-over-ten across a blend of two systems — not an R-multiple.
RISK_BY_SLEEVE = {
    "MOMENTUM": RISK_PER_TRADE_USD,      # 10.0
    "MR":       MR_RISK_PER_TRADE_USD,   #  5.0
    "PROBE":    TEST_SIZE_USD,           # probe legs risk the $20 test notional
}
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
