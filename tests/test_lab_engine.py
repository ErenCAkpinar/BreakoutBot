"""The panel engine, pinned to hand-computable answers.

A backtester that is quietly wrong does not fail — it produces confident numbers
that nobody re-derives, and every strategy built on it inherits the error. These
tests exist so the two properties the engine ENFORCES (the one-bar lag and the
turnover cost) are facts rather than intentions.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "experiments", "lab"))

import engine  # noqa: E402
import panel as panel_mod  # noqa: E402


def _flat(n: int, s: int = 1) -> tuple[np.ndarray, np.ndarray]:
    return np.zeros((n, s)), np.ones((n, s), dtype=bool)


# ── the lag ──────────────────────────────────────────────────────────────────

def test_weights_earn_the_NEXT_bar_return_not_this_one():
    """w[t] is decided on bar t and earns ret[t+1]. If the engine applied w[t] to
    ret[t], every strategy would be reading the close it trades on."""
    ret = np.array([[0.10], [0.20], [0.30]])
    w = np.array([[1.0], [0.0], [0.0]])          # in for exactly one bar
    _, mask = _flat(3)
    r = engine.evaluate(w, ret, mask, cost=0.0)
    # bar 0: nothing held yet. bar 1: holds w[0]=1 → +0.20. bar 2: w[1]=0 → 0.
    assert np.allclose(r, [0.0, 0.20, 0.0]), r


def test_a_strategy_cannot_profit_from_the_bar_it_reads():
    """The decisive one. A 'strategy' that perfectly knows each bar's own return
    must earn NOTHING from that knowledge — only from the next bar."""
    rng = np.random.default_rng(0)
    ret = rng.normal(0, 0.01, size=(500, 1))
    clairvoyant = np.sign(ret)                    # w[t] = sign(ret[t])
    _, mask = _flat(500)
    r = engine.evaluate(clairvoyant, ret, mask, cost=0.0)
    # If the lag were missing this would be sum(|ret|) — hugely positive.
    assert r.sum() < abs(ret).sum() * 0.25, "lookahead leaked through"


def test_next_bar_clairvoyance_DOES_pay():
    """The mirror image: knowing the NEXT bar is legitimately profitable, so the
    previous test is about the lag and not about the engine being inert."""
    rng = np.random.default_rng(1)
    ret = rng.normal(0, 0.01, size=(500, 1))
    w = np.vstack([np.sign(ret[1:]), [[0.0]]])    # w[t] = sign(ret[t+1])
    _, mask = _flat(500)
    r = engine.evaluate(w, ret, mask, cost=0.0)
    assert np.isclose(r.sum(), np.abs(ret[1:]).sum())


# ── the cost ─────────────────────────────────────────────────────────────────

def test_a_full_round_trip_costs_two_sides():
    """In and out is two crossings of the spread, not one."""
    ret = np.zeros((3, 1))
    w = np.array([[1.0], [0.0], [0.0]])
    _, mask = _flat(3)
    r = engine.evaluate(w, ret, mask, cost=0.001)
    assert np.isclose(r.sum(), -0.002), r        # 0→1 then 1→0


def test_holding_still_costs_nothing():
    ret = np.zeros((5, 1))
    w = np.ones((5, 1))
    _, mask = _flat(5)
    r = engine.evaluate(w, ret, mask, cost=0.001)
    assert np.isclose(r.sum(), -0.001)           # only the initial entry


def test_cost_scales_with_the_size_of_the_change():
    ret = np.zeros((2, 1))
    _, mask = _flat(2)
    small = engine.evaluate(np.array([[0.1], [0.1]]), ret, mask, cost=0.001)
    big = engine.evaluate(np.array([[1.0], [1.0]]), ret, mask, cost=0.001)
    assert np.isclose(big.sum(), 10 * small.sum())


# ── the mask ─────────────────────────────────────────────────────────────────

def test_an_unlisted_coin_cannot_be_held():
    """A coin listed mid-window has no price before its first bar. Holding it
    there would be inventing history — the quiet way a panel backtest cheats."""
    ret = np.array([[0.0, 0.5], [0.0, 0.5], [0.0, 0.5]])
    mask = np.array([[True, False], [True, False], [True, True]])
    w = np.ones((3, 2))
    r = engine.evaluate(w, ret, mask, cost=0.0)
    assert np.isclose(r[1], 0.0), "held a coin that was not listed yet"


def test_nan_returns_never_propagate():
    ret = np.array([[np.nan], [0.1], [np.nan]])
    w = np.ones((3, 1))
    _, mask = _flat(3)
    assert np.all(np.isfinite(engine.evaluate(w, ret, mask, cost=0.0)))


# ── summary statistics ───────────────────────────────────────────────────────

def test_equity_compounds_rather_than_sums():
    """-50% then +50% is -25%. A summing curve would call it flat."""
    eq = engine.equity(np.array([-0.5, 0.5]))
    assert np.isclose(eq[-1], 0.75)


def test_max_drawdown_is_measured_from_the_running_peak():
    eq = engine.equity(np.array([0.5, -0.5, 0.0]))     # 1.5 → 0.75
    assert np.isclose(engine.max_drawdown(eq), -0.5)


def test_sharpe_annualises_by_the_timeframe():
    """The reason to annualise at all: 15m and 1d are otherwise incomparable, and
    comparing them is the point of the search."""
    rng = np.random.default_rng(3)
    r = rng.normal(0.0001, 0.01, size=5000)
    fast = engine.stats(r, "1h")["sharpe"]
    slow = engine.stats(r, "1d")["sharpe"]
    assert fast > slow
    ratio = (engine.BARS_PER_YEAR["1h"] / engine.BARS_PER_YEAR["1d"]) ** 0.5
    assert np.isclose(fast / slow, ratio, rtol=1e-9)


# ── resampling ───────────────────────────────────────────────────────────────

def _five_min(n: int) -> pd.DataFrame:
    ts = np.arange(n, dtype=np.int64) * 300_000
    c = np.arange(1, n + 1, dtype=float)
    return pd.DataFrame({"ts": ts, "open": c, "high": c + 1, "low": c - 1,
                         "close": c, "volume": np.ones(n)})


def test_resample_builds_correct_ohlc_bars():
    """Twelve 5m bars make one hour: first open, max high, min low, last close,
    summed volume. An off-by-one here silently shifts every signal."""
    out = panel_mod.resample(_five_min(24), "1h")
    assert len(out) == 2
    assert out["open"].iloc[0] == 1 and out["close"].iloc[0] == 12
    assert out["high"].iloc[0] == 13 and out["low"].iloc[0] == 0
    assert out["volume"].iloc[0] == 12
    assert out["open"].iloc[1] == 13 and out["close"].iloc[1] == 24


def test_resample_labels_bars_by_their_OPEN_time():
    """Left-labelled bins are what make the engine's lag mean what it says: the
    bar stamped T covers [T, T+tf), so its weight can only trade from T+tf."""
    out = panel_mod.resample(_five_min(24), "1h")
    assert out.index[0] == pd.Timestamp("1970-01-01 00:00:00", tz="UTC")
    assert out.index[1] == pd.Timestamp("1970-01-01 01:00:00", tz="UTC")


# ── PBO over a returns matrix ────────────────────────────────────────────────

def test_pbo_matrix_is_high_for_a_grid_of_pure_noise():
    """Whoever wins in-sample among identical noise has no reason to win again,
    so the in-sample best should land below the out-of-sample median about half
    the time. This is the number that tells us a grid search found nothing."""
    import search
    rng = np.random.default_rng(7)
    R = rng.normal(0, 0.01, size=(2000, 20))
    assert search.pbo_matrix(R, blocks=6)["pbo"] > 0.35


def test_pbo_matrix_is_low_when_one_config_genuinely_dominates():
    """The mirror: a real, persistent edge must be recoverable, or the metric
    would condemn everything and mean nothing."""
    import search
    rng = np.random.default_rng(8)
    R = rng.normal(0, 0.01, size=(2000, 20))
    R[:, 3] += 0.004                      # one column with a real drift
    assert search.pbo_matrix(R, blocks=6)["pbo"] < 0.15


def test_pbo_matrix_declines_on_too_few_configs():
    import search
    assert search.pbo_matrix(np.zeros((100, 1)), blocks=6)["pbo"] is None
