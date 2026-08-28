"""The cross-sectional ranking refinements: skip, vol_adj, and hysteresis.

These change WHICH coins are held, so a bug here does not crash — it quietly
produces a different strategy than the one being reported on. The buffer in
particular has to reduce to plain top-k when it is off, or every comparison
against the un-buffered arm is measuring two unrelated things.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "experiments", "lab"))

import panel as panel_mod  # noqa: E402
import strategies as S  # noqa: E402


def _panel(paths: dict[str, list[float]]) -> panel_mod.Panel:
    syms = sorted(paths)
    n = len(next(iter(paths.values())))
    close = np.column_stack([np.array(paths[s], float) for s in syms])
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return panel_mod.Panel(syms, close, close.copy(), close.copy(),
                           np.ones_like(close), idx)


def test_buffer_zero_reproduces_plain_top_k_exactly():
    """The control that makes every buffered-vs-unbuffered comparison valid."""
    rng = np.random.default_rng(4)
    paths = {f"C{i}": list(100 * np.cumprod(1 + rng.normal(0, 0.01, 400)))
             for i in range(8)}
    p = _panel(paths)
    a = S.xs_mom(p, lookback=20, k=3, longshort=True, buffer=0)
    b = S.xs_mom(p, lookback=20, k=3, longshort=True)
    assert np.allclose(a, b)


def test_buffer_reduces_turnover():
    """Its entire purpose. The gross spread measured +1.6% against -2.5% of
    turnover cost, so anything that does not cut turnover is not worth having."""
    rng = np.random.default_rng(5)
    paths = {f"C{i}": list(100 * np.cumprod(1 + rng.normal(0, 0.02, 600)))
             for i in range(10)}
    p = _panel(paths)
    plain = S.xs_mom(p, lookback=20, k=3, longshort=True, buffer=0)
    buff = S.xs_mom(p, lookback=20, k=3, longshort=True, buffer=4)
    t_plain = np.abs(np.diff(plain, axis=0)).sum()
    t_buff = np.abs(np.diff(buff, axis=0)).sum()
    assert t_buff < t_plain, (t_buff, t_plain)


def test_buffer_still_holds_exactly_k_names_per_side():
    rng = np.random.default_rng(6)
    paths = {f"C{i}": list(100 * np.cumprod(1 + rng.normal(0, 0.02, 300)))
             for i in range(10)}
    p = _panel(paths)
    w = S.xs_mom(p, lookback=20, k=3, longshort=True, buffer=4)
    for t in range(60, 300):
        row = w[t]
        assert (row > 0).sum() == 3, f"bar {t}: {(row > 0).sum()} uzun"
        assert (row < 0).sum() == 3, f"bar {t}: {(row < 0).sum()} kısa"


def test_long_and_short_sets_never_overlap():
    """A coin held long and short at once would net to zero exposure while still
    paying both sides of the fee — a pure leak."""
    rng = np.random.default_rng(12)
    paths = {f"C{i}": list(100 * np.cumprod(1 + rng.normal(0, 0.02, 300)))
             for i in range(8)}
    p = _panel(paths)
    w = S.xs_mom(p, lookback=20, k=3, longshort=True, buffer=3)
    for t in range(60, 300):
        assert not (set(np.where(w[t] > 0)[0]) & set(np.where(w[t] < 0)[0]))


def test_skip_ignores_the_most_recent_bars():
    """skip ranks on [t-lookback, t-skip]. A coin that only moves inside the skip
    window must not affect the ranking at all."""
    flat = [100.0] * 60
    spike = [100.0] * 55 + [100.0, 200.0, 200.0, 200.0, 200.0]   # moves late
    p = _panel({"A": flat, "B": spike})
    with_skip = S._score(p, lookback=40, skip=5)
    no_skip = S._score(p, lookback=40, skip=0)
    assert np.isclose(with_skip[59, 1], 0.0), "skip penceresi sızdırdı"
    assert no_skip[59, 1] > 0.5, "skip=0 hareketi görmeliydi"


def test_vol_adj_prefers_the_calmer_of_two_equal_moves():
    """Same total return, different path volatility: the risk-adjusted ranking
    should favour the steady one."""
    n = 200
    rng = np.random.default_rng(2)
    # Both drift the same; only the noise around the drift differs. STEADY needs
    # SOME volatility — a perfectly constant series has zero risk, which is not a
    # thing to risk-adjust, and _score correctly returns NaN for it.
    quiet = rng.normal(0, 0.002, n)
    loud = rng.normal(0, 0.03, n)
    steady = list(100 * np.cumprod(1.001 + quiet - quiet.mean()))
    wild = list(100 * np.cumprod(1.001 + loud - loud.mean()))
    p = _panel({"STEADY": steady, "WILD": wild})
    raw = S._score(p, lookback=50, vol_adj=False)[-1]
    adj = S._score(p, lookback=50, vol_adj=True)[-1]
    i_st, i_wd = p.symbols.index("STEADY"), p.symbols.index("WILD")
    assert abs(raw[i_st] - raw[i_wd]) < 0.25, "kurgu: ham getiriler yakın olmalı"
    assert adj[i_st] > adj[i_wd], "vol_adj sakin olanı tercih etmedi"


def test_score_never_uses_a_future_bar():
    """Truncating the panel must not change the scores that came before."""
    rng = np.random.default_rng(9)
    paths = {f"C{i}": list(100 * np.cumprod(1 + rng.normal(0, 0.01, 300)))
             for i in range(4)}
    full = S._score(_panel(paths), lookback=30, skip=3, vol_adj=True)
    cut = S._score(_panel({k: v[:200] for k, v in paths.items()}),
                   lookback=30, skip=3, vol_adj=True)
    a, b = full[:200], cut
    both = np.isfinite(a) & np.isfinite(b)
    assert np.allclose(a[both], b[both])
