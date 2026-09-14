"""Mechanism tests for experiments/synth/gen.py — does each market law plant
what it claims, and are the frames shaped like the ones the simulator reads?"""
from __future__ import annotations

import numpy as np
import pytest

from experiments.synth import gen
from config import BARS_PER_DAY, REGIME_WARMUP_BARS


def _vr(df, k):
    r = np.log(df["close"]).diff().dropna()
    rk = np.log(df["close"]).diff(k).dropna()
    return float(rk.var() / (k * r.var()))


@pytest.fixture(scope="module")
def null_frames():
    return gen.simulate(gen.SCENARIOS["null"], 30, seed=11)


def test_frame_shape_and_grid(null_frames):
    n = (30 * BARS_PER_DAY) + REGIME_WARMUP_BARS
    ref_ts = None
    for sym, df in null_frames.items():
        assert list(df.columns) == ["ts", "open", "high", "low", "close", "volume"]
        assert len(df) == n
        assert df["ts"].dtype.kind == "i"
        assert (np.diff(df["ts"].to_numpy()) == gen.BAR_MS).all()
        ref_ts = df["ts"].to_numpy() if ref_ts is None else ref_ts
        assert (df["ts"].to_numpy() == ref_ts).all(), "coins must share one clock"
    # the trading window starts exactly at the requested epoch
    assert int(null_frames["BTCUSDT"]["ts"].iloc[REGIME_WARMUP_BARS]) == gen.EPOCH_2027


def test_ohlc_is_valid(null_frames):
    for df in null_frames.values():
        body_hi = np.maximum(df["open"], df["close"])
        body_lo = np.minimum(df["open"], df["close"])
        assert (df["high"] >= body_hi - 1e-12).all()
        assert (df["low"]  <= body_lo + 1e-12).all()
        assert (df["low"] > 0).all() and (df["volume"] > 0).all()
        # continuous: each open is the previous close
        assert np.allclose(df["open"].iloc[1:].to_numpy(), df["close"].iloc[:-1].to_numpy())


def test_seed_is_deterministic():
    a = gen.simulate(gen.SCENARIOS["null"], 5, seed=3)["ADAUSDT"]
    b = gen.simulate(gen.SCENARIOS["null"], 5, seed=3)["ADAUSDT"]
    c = gen.simulate(gen.SCENARIOS["null"], 5, seed=4)["ADAUSDT"]
    assert a.equals(b)
    assert not a["close"].equals(c["close"])


def test_alts_load_on_the_btc_factor(null_frames):
    btc = np.log(null_frames["BTCUSDT"]["close"]).diff().dropna()
    ada = np.log(null_frames["ADAUSDT"]["close"]).diff().dropna()
    corr = float(np.corrcoef(btc, ada)[0, 1])
    assert 0.5 < corr < 0.9, corr          # real ADA/BTC 5m corr ≈ 0.75


def test_wick_calibration(null_frames):
    df = null_frames["ADAUSDT"]
    hl = ((df["high"] - df["low"]) / df["close"]).mean()
    co = ((df["close"] - df["open"]).abs() / df["close"]).mean()
    assert 1.85 < hl / co < 2.3            # real ≈ 2.09


def test_null_is_a_random_walk():
    df = gen.simulate(gen.Scenario("x", garch=(0, 0)), 200, seed=5)["ADAUSDT"]
    assert abs(_vr(df, 48) - 1) < 0.08
    assert abs(_vr(df, 288) - 1) < 0.2


def test_trend_plants_intraday_momentum():
    df = gen.simulate(gen.SCENARIOS["trend"], 200, seed=5)["ADAUSDT"]
    assert _vr(df, 48) > 1.10
    assert _vr(df, 96) > 1.20


def test_trend_slow_is_invisible_intraday_but_visible_daily():
    df = gen.simulate(gen.SCENARIOS["trend_slow"], 300, seed=5)["ADAUSDT"]
    assert _vr(df, 48) < 1.10
    assert _vr(df, 1440) > 1.20


def test_chop_plants_mean_reversion():
    df = gen.simulate(gen.SCENARIOS["chop"], 200, seed=5)["ADAUSDT"]
    assert _vr(df, 288) < 0.85
    assert _vr(df, 1440) < 0.5


def test_markov_mix_matches_target():
    rng = np.random.default_rng(0)
    seq = gen._markov_states(rng, 20_000, gen.YEAR_MIX["2028"])
    for s, p in gen.YEAR_MIX["2028"].items():
        assert abs(seq.count(s) / len(seq) - p) < 0.05


def test_regime_frames_use_per_day_laws():
    frames, seq = gen.simulate_regime({"UP": 0.5, "CHOP": 0.5, "DOWN": 0.0}, 10, seed=2)
    assert len(seq) == 10 + REGIME_WARMUP_BARS // BARS_PER_DAY
    assert set(seq) <= {"UP", "CHOP"}
    assert len(frames["ADAUSDT"]) == (10 + 40) * BARS_PER_DAY


def test_bootstrap_keeps_real_bar_shapes_and_joint_blocks():
    real = gen.load_real(240)
    out = gen.bootstrap(real, 10, seed=9, block_days=3)
    n = (10 + 40) * BARS_PER_DAY
    for sym, df in out.items():
        assert len(df) == n
        assert (np.diff(df["ts"].to_numpy()) == gen.BAR_MS).all()
        assert (df["high"] >= np.maximum(df["open"], df["close"]) - 1e-9).all()
    # first synthetic day of ADA must be a rescaled copy of SOME real ADA day:
    # the high/open ratio sequence identifies the day, scale drops out.
    ada = out["ADAUSDT"].iloc[:BARS_PER_DAY]
    sig = (ada["high"] / ada["open"]).round(6).to_numpy()
    r = real["ADAUSDT"]
    day = (r["ts"] // 86_400_000).to_numpy()
    found = None
    for d in np.unique(day):
        g = r[day == d]
        if len(g) != BARS_PER_DAY:
            continue
        if np.allclose((g["high"] / g["open"]).round(6).to_numpy(), sig):
            found = d
            break
    assert found is not None
    # the SAME real day was used for BTC (joint sampling preserves correlation)
    btc = out["BTCUSDT"].iloc[:BARS_PER_DAY]
    gb  = real["BTCUSDT"][day == found]
    assert np.allclose((btc["high"] / btc["open"]).to_numpy(), (gb["high"] / gb["open"]).to_numpy())
    # volume is the real day's volume, untouched
    assert np.allclose(ada["volume"].to_numpy(), r[day == found]["volume"].to_numpy())


def test_chop_anchor_follows_the_price_after_a_trend():
    """A CHOP regime after an UP regime must revert toward where the trend left
    the price, not toward day 0 (the v1 bug that snapped every rally back)."""
    up   = gen.Scenario("UP", drift_ann=6.0, trend_k=0.0, garch=(0, 0))   # +600%/yr: unmistakable
    chop = gen.Scenario("CHOP", mr_hl_d=0.5, garch=(0, 0))
    warm = REGIME_WARMUP_BARS // BARS_PER_DAY
    laws = [up] * (warm + 20) + [chop] * 20
    df = gen.simulate(laws, 40, seed=1)["BTCUSDT"]
    c = df["close"].to_numpy()
    p_start = c[0]
    p_switch = c[(warm + 20) * BARS_PER_DAY - 1]
    p_chop_mean = c[(warm + 20) * BARS_PER_DAY:].mean()
    assert p_switch > 1.3 * p_start                       # the trend happened
    assert abs(np.log(p_chop_mean / p_switch)) < abs(np.log(p_chop_mean / p_start))
    assert abs(np.log(p_chop_mean / p_switch)) < 0.15     # stays near the switch level


def test_martingale_null_has_zero_arithmetic_drift():
    """The plain null is a log-martingale (+σ²/2 arithmetic drift per bar); the
    martingale null must have ~zero arithmetic drift so a long-only system
    cannot earn from holding alone."""
    plain = gen.simulate(gen.Scenario("p", garch=(0, 0)), 300, seed=7)["ADAUSDT"]
    mart  = gen.simulate(gen.Scenario("m", garch=(0, 0), martingale=True), 300, seed=7)["ADAUSDT"]
    rp = plain["close"].pct_change().dropna()
    rm = mart["close"].pct_change().dropna()
    se = float(rp.std() / np.sqrt(len(rp)))
    sigma2_half = 0.5 * gen.DEFAULT_COINS[3].sigma ** 2        # ADA is index 3
    # same shocks (same seed): the difference in mean return is the removed drift
    assert abs((rp.mean() - rm.mean()) - sigma2_half) < 0.2 * sigma2_half
    assert abs(rm.mean()) < 3 * se


def test_random_entry_state_is_a_coin_flip_that_always_confirms():
    from experiments.synth.random_entry import RandomEntryState
    st = RandomEntryState(symbol="ADAUSDT", p_entry=0.01, seed=3)
    sig = [st.engine.analyze({})["signal"] for _ in range(20_000)]
    rate = sig.count("STRONG_LONG") / len(sig)
    assert 0.007 < rate < 0.013 and set(sig) <= {"STRONG_LONG", "NEUTRAL"}
    assert st._confirm(1.0, 0.0, 0.0, 1) is True
    # deterministic across processes: same seed+symbol → same stream
    st2 = RandomEntryState(symbol="ADAUSDT", p_entry=0.01, seed=3)
    assert [st2.engine.analyze({})["signal"] for _ in range(200)] == sig[:200]
    st3 = RandomEntryState(symbol="ADAUSDT", p_entry=0.01, seed=4)
    assert [st3.engine.analyze({})["signal"] for _ in range(200)] != sig[:200]


def test_xs_book_accounting_on_a_known_panel():
    """Long-short book: top-K long, bottom-K short, cost on turnover only."""
    from experiments.synth import xs_mom
    # 10 coins; every coin's 'signal' return equals its index (coin 9 leads),
    # and the hold return is the same ordering scaled — a perfect momentum panel.
    n_s = 10
    lc = np.zeros((xs_mom.LOOK + xs_mom.HOLD + 1, n_s))
    lc[xs_mom.LOOK] = np.arange(n_s) * 0.001            # signal: coin j moved j·0.1%
    lc[xs_mom.LOOK + xs_mom.HOLD] = lc[xs_mom.LOOK] + np.arange(n_s) * 0.01
    pts = np.array([xs_mom.LOOK])
    b = xs_mom.run_book(lc, pts, "ls", cost=0.001)
    r = np.exp(np.arange(n_s) * 0.01) - 1
    expect = 0.1 * r[-5:].sum() - 0.1 * r[:5].sum() - 0.001 * 1.0   # gross 100% turnover
    assert abs(b["r"][0] - expect) < 1e-9
    assert b["cost"][0] == 0.001
    ew = xs_mom.run_book(lc, pts, "ew", cost=0.001)
    assert abs(ew["r"][0] - (r.mean() - 0.001)) < 1e-9


def test_xs_random_ranking_is_information_free():
    """With a random ranking the expected gross is the equal-weight mean of the
    long minus short legs ≈ 0 on a symmetric panel; only the cost remains."""
    from experiments.synth import xs_mom
    rng = np.random.default_rng(0)
    n_t, n_s = 20_000, 8
    lc = np.cumsum(rng.normal(0, 0.01, (n_t, n_s)), axis=0)
    pts = np.arange(xs_mom.LOOK, n_t - xs_mom.HOLD, xs_mom.HOLD)
    b = xs_mom.run_book(lc, pts, "rand", np.random.default_rng(1), cost=0.0)
    assert abs(b["r"].mean()) < 4 * b["r"].std(ddof=1) / np.sqrt(len(pts))
