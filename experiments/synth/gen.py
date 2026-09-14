"""Synthetic 5m OHLCV markets for the deployed simulator.

WHY SYNTHETIC
-------------
Every backtest in this repo replays the ONE path history happened to take. That
path cannot answer the question the live account keeps asking — "is the flat
equity curve a broken machine or a market with nothing in it?" — because on real
data the two look identical. A market we control can separate them:

  · on a driftless random walk (`null`) NO strategy can have an edge. The only
    honest result is −friction. Anything better is lookahead; anything much
    worse is a structural cost in the exit geometry, probes or gates.
  · on a market with a PLANTED persistent trend (`trend`) a breakout system MUST
    profit. If it cannot harvest an edge that is there by construction, the
    entry/exit machinery is the problem, not the market.
  · on a mean-reverting market (`chop`) breakouts must lose; the interesting
    number is how much — the SL/TP geometry bounds it.
  · `regime` chains those three through a Markov switch with a chosen mix, which
    is the only defensible meaning of "2027–2030 price data": scenarios with
    stated assumptions, not forecasts.
  · `bootstrap` re-orders REAL days (jointly across coins) and chains them, so
    every bar shape, volume profile and cross-coin correlation is real and only
    the multi-day sequencing is randomised.

CALIBRATION
-----------
Per-bar vol, BTC beta, the intraday vol/volume profile and vol clustering come
from the cached real frames (backtests/data/<sym>_240d+40w.pkl) and were checked
against them on 2026-09-13: 5m sd 0.14% (BTC) – 0.31% (NEAR), alt beta 1.0–1.3,
range/|close−open| ≈ 2.0, |r| autocorr 0.26 (lag 1) / 0.11 (lag 288), volume
peaking 13–17 UTC at ~1.6× and corr(volume, |r|) ≈ 0.6. Bars are built from 20
sub-steps so the high/low range has the right size relative to the close move.

Output frames carry exactly the columns backtest.run_portfolio needs
(ts, open, high, low, close, volume), REGIME_WARMUP_BARS of prefix included.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import BARS_PER_DAY, REGIME_WARMUP_BARS, TOKENS  # noqa: E402

CACHE      = os.path.join(ROOT, "backtests", "data")
SUBSTEPS   = 20                      # sub-steps per 5m bar → realistic high/low
WICK_STRETCH = 1.45                  # calibrated: range/|c−o| 1.69 → ≈2.1 (real 2.09)
BAR_MS     = 300_000
EPOCH_2027 = 1_798_761_600_000       # 2027-01-01T00:00:00Z, a 5m boundary

# Measured on the real 240d cache (see module docstring). Hour → multiplier of
# the daily mean; same shape is used for vol and volume (they co-move, ρ≈0.6).
INTRADAY = np.array([1.06, 0.92, 0.87, 0.84, 0.83, 0.88, 0.84, 0.84, 0.92, 0.87,
                     0.84, 0.83, 1.01, 1.27, 1.64, 1.61, 1.44, 1.16, 1.06, 0.93,
                     0.87, 0.76, 0.87, 0.84])
INTRADAY  = INTRADAY / INTRADAY.mean()


@dataclass
class CoinSpec:
    symbol:  str
    price:   float      # starting price
    sigma:   float      # total 5m log-return sd
    beta:    float      # loading on the BTC factor
    volume:  float      # mean 5m volume (base units)
    tick_dp: int = 4    # decimals to round prices to


# Real values as of the 2026-09 cache; the generator only needs the right ORDER
# of magnitude, the simulator sizes in USD off ATR.
DEFAULT_COINS: list[CoinSpec] = [
    CoinSpec("BTCUSDT",  110_000.0, 0.00142, 1.00, 1_300.0,  1),
    CoinSpec("UNIUSDT",       8.0,  0.00277, 1.15, 320_000.0, 3),
    CoinSpec("INJUSDT",      12.0,  0.00304, 1.28, 210_000.0, 3),
    CoinSpec("ADAUSDT",       0.8,  0.00237, 1.25, 4_800_000.0, 4),
    CoinSpec("POLUSDT",       0.25, 0.00234, 1.00, 9_000_000.0, 4),
    CoinSpec("NEARUSDT",      2.5,  0.00309, 1.31, 1_400_000.0, 3),
]
FACTOR_SIGMA = 0.00142               # BTC 5m sd; alts load beta× on it


@dataclass
class Scenario:
    """One market law. Everything is per 5m bar unless stated.

    drift_ann    : constant annualised log drift on the factor (0 = driftless)
    trend_k      : planted momentum — sd of the latent expected DAILY return as a
                   fraction of daily vol. 0 = none. Its horizon is trend_hl_d;
                   see the VR table above SCENARIOS for what each pair plants.
    trend_hl_d   : half-life (days) of that latent drift
    mr_hl_d      : mean reversion — half-life (days) of log-price deviations from
                   a slow anchor. 0 = none. 1.0 gives VR(1d) ≈ 0.6.
    vol_mult     : scales all vols
    garch        : (alpha, beta) of a GARCH(1,1) on the shocks; (0,0) = constant
    """
    name:       str
    drift_ann:  float = 0.0
    trend_k:    float = 0.0
    trend_hl_d: float = 2.0
    mr_hl_d:    float = 0.0
    vol_mult:   float = 1.0
    garch:      tuple = (0.08, 0.90)
    martingale: bool = False   # True: zero ARITHMETIC drift (log drift −σ²/2 per
                               # bar). The plain null is a log-martingale, which
                               # hands a long-only system +σ²/2 per bar held —
                               # ≈ +0.03–0.05R per position at 8–24h holds.


# Variance ratios measured on 3×400d samples (ADAUSDT, GARCH off):
#   null        VR 4h 1.01 · 8h 1.01 · 1d 1.02 · 5d 1.02   (a random walk)
#   trend       VR 4h 1.22 · 8h 1.39 · 1d 1.76 · 5d 2.07   (momentum AT the bot's
#               1h–8h holding horizon — the edge a 5m breakout is built to take)
#   trend_slow  VR 4h 1.04 · 8h 1.08 · 1d 1.17 · 5d 1.48   (multi-day momentum
#               only; invisible inside an 8h hold, visible to the 4h regime gate)
#   chop        VR 4h 0.95 · 8h —    · 1d 0.72 · 5d 0.30   (mean reversion)
# For reference the REAL 240d window reads VR 4h 0.93 · 1d 0.97 · 5d 0.98.
SCENARIOS: dict[str, Scenario] = {
    "null":       Scenario("null"),
    "null_mart":  Scenario("null_mart", martingale=True),
    "trend":      Scenario("trend",      trend_k=1.0, trend_hl_d=0.25),
    "trend_slow": Scenario("trend_slow", trend_k=0.3, trend_hl_d=2.0),
    "chop":       Scenario("chop",       mr_hl_d=1.0),
    "bull":       Scenario("bull",       drift_ann=1.5,  trend_k=0.6, trend_hl_d=0.5),
    "bear":       Scenario("bear",       drift_ann=-1.0, trend_k=0.6, trend_hl_d=0.5,
                           vol_mult=1.3),
}

# Regime states for the Markov scenarios. Mean dwell ≈ 15/(1−p_state) days.
REGIME_STATES: dict[str, Scenario] = {
    "UP":   Scenario("UP",   drift_ann=1.5,  trend_k=0.6, trend_hl_d=0.5),
    "DOWN": Scenario("DOWN", drift_ann=-1.2, trend_k=0.6, trend_hl_d=0.5, vol_mult=1.3),
    "CHOP": Scenario("CHOP", mr_hl_d=1.0, vol_mult=0.85),
}

# "2027–2030" = four scenario YEARS with a stated regime mix (stationary
# distribution of the Markov chain). They are assumptions, not forecasts.
YEAR_MIX: dict[str, dict[str, float]] = {
    "2027": {"UP": 0.55, "DOWN": 0.15, "CHOP": 0.30},   # expansion year
    "2028": {"UP": 0.15, "DOWN": 0.45, "CHOP": 0.40},   # drawdown year
    "2029": {"UP": 0.20, "DOWN": 0.20, "CHOP": 0.60},   # range year
    "2030": {"UP": 0.40, "DOWN": 0.20, "CHOP": 0.40},   # recovery year
}


# ── Core simulation ─────────────────────────────────────────────────────────

def _garch_path(rng: np.random.Generator, n: int, alpha: float, beta: float) -> np.ndarray:
    """Multiplicative vol path with unit unconditional variance."""
    if alpha <= 0 and beta <= 0:
        return np.ones(n)
    omega = 1.0 - alpha - beta
    h = np.empty(n)
    z = rng.standard_normal(n)
    h[0] = 1.0
    for t in range(1, n):
        h[t] = omega + alpha * h[t - 1] * z[t - 1] ** 2 + beta * h[t - 1]
    return np.sqrt(h)


def _latent_drift(rng: np.random.Generator, n_bars: int, k: float, hl_days: float,
                  daily_sd: float) -> np.ndarray:
    """OU expected return per bar: sd k·daily_sd (at 1d), half-life hl_days."""
    if k <= 0:
        return np.zeros(n_bars)
    phi   = float(np.exp(-np.log(2) / (hl_days * BARS_PER_DAY)))
    sd_d  = k * daily_sd                   # sd of expected DAILY return
    sd_b  = sd_d / BARS_PER_DAY            # per-bar expected return sd
    m     = np.empty(n_bars)
    m[0]  = rng.normal(0, sd_b)
    eps   = rng.normal(0, sd_b * np.sqrt(1 - phi ** 2), n_bars)
    for t in range(1, n_bars):
        m[t] = phi * m[t - 1] + eps[t]
    return m


def _markov_states(rng: np.random.Generator, n_days: int, mix: dict[str, float],
                   mean_dwell_d: float = 15.0) -> list[str]:
    """Day-level state sequence whose stationary distribution is `mix`."""
    names = list(mix)
    p     = np.array([mix[s] for s in names])
    p     = p / p.sum()
    stay  = 1.0 - 1.0 / mean_dwell_d
    # P = stay·I + (1−stay)·1pᵀ  has stationary distribution p exactly.
    P = stay * np.eye(len(names)) + (1 - stay) * np.tile(p, (len(names), 1))
    s = rng.choice(len(names), p=p)
    out = []
    for _ in range(n_days):
        out.append(names[s])
        s = rng.choice(len(names), p=P[s])
    return out


def simulate(scn: Scenario | list[Scenario], days: int, seed: int,
             coins: list[CoinSpec] | None = None,
             warmup_bars: int = REGIME_WARMUP_BARS,
             start_ts: int = EPOCH_2027) -> dict[str, pd.DataFrame]:
    """Joint 5m OHLCV paths for all coins under one law (or a per-day list).

    `scn` may be a list with one Scenario per DAY (regime switching); the list
    must cover warmup + days.
    """
    coins  = coins or DEFAULT_COINS
    rng    = np.random.default_rng(seed)
    n_days = days + warmup_bars // BARS_PER_DAY
    n_bars = n_days * BARS_PER_DAY
    n_sub  = n_bars * SUBSTEPS
    laws   = scn if isinstance(scn, list) else [scn] * n_days
    assert len(laws) >= n_days, "one Scenario per day required"

    ts     = start_ts - warmup_bars * BAR_MS + np.arange(n_bars, dtype=np.int64) * BAR_MS
    hour   = ((ts // 3_600_000) % 24).astype(int)
    intra  = np.repeat(INTRADAY[hour], SUBSTEPS)         # per sub-step
    day_ix = np.repeat(np.arange(n_days), BARS_PER_DAY)

    # Per-day law → per-bar arrays
    drift  = np.array([laws[d].drift_ann for d in day_ix]) / (365 * BARS_PER_DAY)
    volm   = np.array([laws[d].vol_mult  for d in day_ix])
    mart   = np.array([laws[d].martingale for d in day_ix])
    k_arr  = np.array([laws[d].trend_k   for d in day_ix])
    hl_arr = np.array([laws[d].trend_hl_d for d in day_ix])
    mr_arr = np.array([laws[d].mr_hl_d   for d in day_ix])
    g_a, g_b = laws[0].garch

    # Common factor shock (BTC), sub-step resolution
    f_vol = _garch_path(rng, n_bars, g_a, g_b)
    zF    = rng.standard_normal(n_sub) * np.repeat(f_vol * volm, SUBSTEPS) * intra
    zF   *= FACTOR_SIGMA / np.sqrt(SUBSTEPS)

    # Latent trend on the factor (shared) — computed at bar resolution
    daily_sd_F = FACTOR_SIGMA * np.sqrt(BARS_PER_DAY)
    mF = _latent_drift(rng, n_bars, float(k_arr.max()), float(hl_arr.mean()), daily_sd_F)
    mF = mF * (k_arr / max(k_arr.max(), 1e-12))     # zero where the day's law has no trend

    out: dict[str, pd.DataFrame] = {}
    for c in coins:
        idio = float(np.sqrt(max(c.sigma ** 2 - (c.beta * FACTOR_SIGMA) ** 2, 1e-12)))
        i_vol = _garch_path(rng, n_bars, g_a, g_b)
        zI = rng.standard_normal(n_sub) * np.repeat(i_vol * volm, SUBSTEPS) * intra
        zI *= idio / np.sqrt(SUBSTEPS)
        daily_sd_i = c.sigma * np.sqrt(BARS_PER_DAY)
        mI = _latent_drift(rng, n_bars, float(k_arr.max()), float(hl_arr.mean()), daily_sd_i)
        mI = mI * (k_arr / max(k_arr.max(), 1e-12))

        mu_bar = drift + c.beta * mF + mI                # per-bar expected log return
        # Price-martingale null: cancel the +σ²/2 arithmetic drift of a
        # log-martingale (unconditional per-bar variance ≈ σ_i² · vol_mult²).
        mu_bar = mu_bar - np.where(mart, 0.5 * (c.sigma * volm) ** 2, 0.0)
        shock  = c.beta * zF + zI                        # per sub-step
        r_sub  = shock + np.repeat(mu_bar / SUBSTEPS, SUBSTEPS)

        # Mean reversion: pull log price toward a slow anchor (random walk with
        # 1/10 of the vol). Done sequentially at bar resolution. On days whose
        # law has no mean reversion the anchor FOLLOWS the price, so a CHOP
        # regime that starts after a trend reverts toward where the trend left
        # the price — not toward the level the series started at. (v1 pinned
        # the anchor at the start price; every UP→CHOP switch then snapped the
        # market back to day 0. The 2027–2030 v1 runs were discarded for it.)
        logp = np.empty(n_sub)
        lp   = np.log(c.price)
        if mr_arr.max() > 0:
            anchor = lp
            a_step = rng.normal(0, c.sigma * 0.1, n_bars)
            for b in range(n_bars):
                hl = mr_arr[b]
                theta = (np.log(2) / (hl * BARS_PER_DAY)) if hl > 0 else 0.0
                anchor = anchor + a_step[b] if hl > 0 else lp
                pull = -theta * (lp - anchor) / SUBSTEPS
                seg  = r_sub[b * SUBSTEPS:(b + 1) * SUBSTEPS] + pull
                lp_path = lp + np.cumsum(seg)
                logp[b * SUBSTEPS:(b + 1) * SUBSTEPS] = lp_path
                lp = lp_path[-1]
        else:
            logp = lp + np.cumsum(r_sub)

        px = np.exp(logp).reshape(n_bars, SUBSTEPS)
        close = px[:, -1]
        open_ = np.concatenate([[c.price], close[:-1]])
        high  = np.maximum(px.max(axis=1), open_)
        low   = np.minimum(px.min(axis=1), open_)
        # 20 sub-steps give range/|close−open| ≈ 1.7; real 5m bars show ≈ 2.1
        # (microstructure wicks). Stretch the wicks, not the bodies, to match —
        # under-sized wicks would under-count stop-outs and flatter the system.
        body_hi = np.maximum(open_, close)
        body_lo = np.minimum(open_, close)
        high = body_hi + (high - body_hi) * WICK_STRETCH
        low  = body_lo - (body_lo - low) * WICK_STRETCH

        # Volume: intraday profile × lognormal noise × (1 + |bar return|/σ)
        bar_ret = np.abs(np.log(close / open_))
        vol = (c.volume * INTRADAY[hour]
               * np.exp(rng.normal(-0.18, 0.6, n_bars))
               * (0.6 + 0.4 * bar_ret / (c.sigma * volm)))

        df = pd.DataFrame({
            "ts":     ts,
            "open":   np.round(open_, c.tick_dp),
            "high":   np.round(high,  c.tick_dp),
            "low":    np.round(low,   c.tick_dp),
            "close":  np.round(close, c.tick_dp),
            "volume": np.round(vol, 1),
        })
        out[c.symbol] = df
    return out


def simulate_regime(mix: dict[str, float], days: int, seed: int,
                    states: dict[str, Scenario] | None = None,
                    mean_dwell_d: float = 15.0,
                    **kw) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Markov-switching market with a stated stationary mix of REGIME_STATES."""
    states = states or REGIME_STATES
    rng    = np.random.default_rng(seed + 7_777)
    n_days = days + REGIME_WARMUP_BARS // BARS_PER_DAY
    seq    = _markov_states(rng, n_days, mix, mean_dwell_d)
    laws   = [states[s] for s in seq]
    return simulate(laws, days, seed, **kw), seq


# ── Block bootstrap of real history ──────────────────────────────────────────

def load_real(days: int = 240, symbols: list[str] | None = None,
              before: str | None = None, after: str | None = None) -> dict[str, pd.DataFrame]:
    """Cached real frames, aligned on a common ts grid (inner join).

    `before` / `after` (YYYY-MM-DD, UTC) cut the source to a date range — used
    to bootstrap from days OUTSIDE the window a geometry was selected on.
    """
    symbols = symbols or (list(TOKENS) + ["BTCUSDT"])
    frames = {}
    for s in symbols:
        p = os.path.join(CACHE, f"{s}_{days}d+40w.pkl")
        frames[s] = pd.read_pickle(p)
    common = None
    for f in frames.values():
        common = set(f["ts"]) if common is None else common & set(f["ts"])
    keep = np.array(sorted(common or set()))
    if before:
        keep = keep[keep < int(pd.Timestamp(before, tz="UTC").timestamp() * 1000)]
    if after:
        keep = keep[keep >= int(pd.Timestamp(after, tz="UTC").timestamp() * 1000)]
    for s in list(frames):
        f = frames[s]
        f = f[f["ts"].isin(keep)].sort_values("ts").reset_index(drop=True)
        frames[s] = f
    return frames


def bootstrap(real: dict[str, pd.DataFrame], days: int, seed: int,
              block_days: int = 5, drift_ann: float = 0.0,
              warmup_bars: int = REGIME_WARMUP_BARS,
              start_ts: int = EPOCH_2027) -> dict[str, pd.DataFrame]:
    """Chain randomly ordered REAL day-blocks (same blocks for every coin).

    Each block is rescaled so its first open equals the running price; every
    bar keeps its real shape (open/high/low/close ratios) and volume. Blocks are
    aligned to UTC midnight so the session filter sees real intraday structure.
    `drift_ann` adds a constant log drift (a bull/bear overlay).
    """
    rng   = np.random.default_rng(seed)
    n_days = days + warmup_bars // BARS_PER_DAY
    ref   = next(iter(real.values()))
    day0  = (ref["ts"] // 86_400_000).to_numpy()
    days_avail = np.unique(day0)
    # only full days
    full = [d for d in days_avail if (day0 == d).sum() == BARS_PER_DAY]
    blocks = [full[i:i + block_days] for i in range(0, len(full) - block_days + 1)]
    picks: list[int] = []
    while len(picks) < n_days:
        b = blocks[rng.integers(len(blocks))]
        picks.extend(b)
    picks = picks[:n_days]

    per_bar_drift = drift_ann / (365 * BARS_PER_DAY)
    out: dict[str, pd.DataFrame] = {}
    for s, f in real.items():
        by_day = {d: g for d, g in f.groupby(day0)}
        parts = []
        price = float(f["close"].iloc[-1])
        for d in picks:
            g = by_day[d]
            base = float(g["open"].iloc[0])
            scale = price / base
            grow = np.exp(per_bar_drift * np.arange(1, BARS_PER_DAY + 1))
            o = g["open"].to_numpy()  * scale * np.concatenate([[1.0], grow[:-1]])
            h = g["high"].to_numpy()  * scale * grow
            lo = g["low"].to_numpy()  * scale * grow
            c = g["close"].to_numpy() * scale * grow
            parts.append(pd.DataFrame({"open": o, "high": h, "low": lo, "close": c,
                                       "volume": g["volume"].to_numpy()}))
            price = float(c[-1])
        df = pd.concat(parts, ignore_index=True)
        df.insert(0, "ts", start_ts - warmup_bars * BAR_MS
                  + np.arange(len(df), dtype=np.int64) * BAR_MS)
        out[s] = df
    return out


# ── Diagnostics of a generated market ────────────────────────────────────────

def describe(frames: dict[str, pd.DataFrame], sym: str = "ADAUSDT") -> dict:
    """Variance ratios and Hurst so a scenario can be checked for what it claims."""
    from indicators import hurst_exponent
    f = frames[sym]
    r = np.log(f["close"]).diff().dropna()

    def vr(k: int) -> float:
        rk = np.log(f["close"]).diff(k).dropna()
        return float(rk.var() / (k * r.var()))

    Hs = [hurst_exponent(f["close"].iloc[i - 200:i]) for i in range(200, len(f), 288)]
    hl = ((f["high"] - f["low"]) / f["close"]).mean()
    co = ((f["close"] - f["open"]).abs() / f["close"]).mean()
    return {"sd5m_pct": round(float(r.std() * 100), 3),
            "VR_1h": round(vr(12), 2), "VR_4h": round(vr(48), 2), "VR_8h": round(vr(96), 2),
            "VR_1d": round(vr(288), 2), "VR_5d": round(vr(1440), 2),
            "hurst_mean": round(float(np.mean(Hs)), 3),
            "range_over_co": round(float(hl / co), 2),
            "total_ret_pct": round(float(f["close"].iloc[-1] / f["close"].iloc[0] - 1) * 100, 1)}
