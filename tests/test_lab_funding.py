"""Funding: the sign convention, the bar alignment, and the two new families.

A wrong sign here does not crash — it turns a cost into an income and makes a
losing carry book look like a winning one. Since the whole reason for adding
funding is that four rounds of price-only search found nothing, a silent sign
error would manufacture exactly the result we are hoping for.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "experiments", "lab"))

import engine  # noqa: E402
import panel as panel_mod  # noqa: E402
import strategies as S  # noqa: E402


def _panel(paths: dict[str, list[float]], funding=None) -> panel_mod.Panel:
    syms = sorted(paths)
    n = len(next(iter(paths.values())))
    close = np.column_stack([np.array(paths[s], float) for s in syms])
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    p = panel_mod.Panel(syms, close, close.copy(), close.copy(),
                        np.ones_like(close), idx)
    if funding is not None:
        p.funding = np.column_stack([np.array(funding[s], float) for s in syms])
    return p


# ── the sign ─────────────────────────────────────────────────────────────────

def test_a_short_is_PAID_when_funding_is_positive():
    """The exchange's convention: positive rate means longs pay shorts. Getting
    this backwards would turn every carry result inside out."""
    ret = np.zeros((3, 1))
    mask = np.ones((3, 1), dtype=bool)
    fund = np.full((3, 1), 0.01)              # +1% per bar, longs pay
    short = np.full((3, 1), -1.0)
    r = engine.evaluate(short, ret, mask, cost=0.0, funding=fund)
    assert np.isclose(r[1:].sum(), 0.02), r    # two bars held, paid each


def test_a_long_PAYS_when_funding_is_positive():
    ret = np.zeros((3, 1))
    mask = np.ones((3, 1), dtype=bool)
    fund = np.full((3, 1), 0.01)
    r = engine.evaluate(np.ones((3, 1)), ret, mask, cost=0.0, funding=fund)
    assert np.isclose(r[1:].sum(), -0.02)


def test_negative_funding_reverses_who_pays():
    ret = np.zeros((3, 1))
    mask = np.ones((3, 1), dtype=bool)
    fund = np.full((3, 1), -0.01)             # shorts pay longs
    r = engine.evaluate(np.ones((3, 1)), ret, mask, cost=0.0, funding=fund)
    assert np.isclose(r[1:].sum(), 0.02)


def test_funding_uses_the_same_lag_as_price():
    """A weight decided on bar t earns bar t+1's funding, not bar t's — otherwise
    a strategy could read the settlement it is about to trade into."""
    ret = np.zeros((3, 1))
    mask = np.ones((3, 1), dtype=bool)
    fund = np.array([[0.05], [0.01], [0.01]])   # a big one on the entry bar
    w = np.array([[-1.0], [0.0], [0.0]])
    r = engine.evaluate(w, ret, mask, cost=0.0, funding=fund)
    assert np.isclose(r[0], 0.0), "bar 0'da henüz pozisyon yok"
    assert np.isclose(r[1], 0.01), "giriş barının funding'ini toplamamalı"


def test_a_flat_book_neither_pays_nor_receives():
    ret = np.zeros((4, 2))
    mask = np.ones((4, 2), dtype=bool)
    fund = np.full((4, 2), 0.01)
    r = engine.evaluate(np.zeros((4, 2)), ret, mask, cost=0.0, funding=fund)
    assert np.allclose(r, 0.0)


def test_funding_is_ignored_when_not_supplied():
    """Every pre-funding result must stay reproducible."""
    rng = np.random.default_rng(1)
    ret = rng.normal(0, 0.01, (50, 3))
    mask = np.ones((50, 3), dtype=bool)
    w = rng.normal(0, 0.3, (50, 3))
    a = engine.evaluate(w, ret, mask, cost=0.0)
    b = engine.evaluate(w, ret, mask, cost=0.0, funding=None)
    assert np.allclose(a, b)


# ── bar alignment ────────────────────────────────────────────────────────────

def test_settlements_are_SUMMED_into_the_bar_that_contains_them(tmp_path, monkeypatch):
    """Funding is a per-settlement amount, not a per-bar rate. Binance pays every
    8h on most pairs and every 4h on some, so a 1d bar must carry the SUM of the
    settlements inside it — reindexing instead would silently drop two thirds."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("experiments/lab/funding", exist_ok=True)
    recs = [{"ts": int(pd.Timestamp(f"2024-01-01 {h:02d}:00", tz="UTC").timestamp() * 1000),
             "rate": 0.001} for h in (0, 8, 16)]
    with open("experiments/lab/funding/XUSDT.json", "w") as fh:
        json.dump({"symbol": "XUSDT", "records": recs}, fh)

    idx = pd.date_range("2024-01-01", periods=2, freq="1D", tz="UTC")
    f = panel_mod.load_funding(["XUSDT"], idx, "1d")
    assert np.isclose(f[0, 0], 0.003), f      # three settlements in one day
    assert np.isclose(f[1, 0], 0.0)


def test_a_symbol_without_funding_data_contributes_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("experiments/lab/funding", exist_ok=True)
    with open("experiments/lab/funding/AUSDT.json", "w") as fh:
        json.dump({"symbol": "AUSDT", "records": [
            {"ts": int(pd.Timestamp("2024-01-01", tz="UTC").timestamp() * 1000),
             "rate": 0.001}]}, fh)
    idx = pd.date_range("2024-01-01", periods=2, freq="1D", tz="UTC")
    f = panel_mod.load_funding(["AUSDT", "BUSDT"], idx, "1d")
    assert np.isclose(f[0, 0], 0.001)
    assert np.allclose(f[:, 1], 0.0)


# ── the families ─────────────────────────────────────────────────────────────

def _flatprices(n: int, syms: list[str]) -> dict[str, list[float]]:
    return {s: [100.0] * n for s in syms}


def test_xs_funding_shorts_the_payers_and_longs_the_receivers():
    """With prices held flat the only thing left is the funding spread, so the
    book must end up short the high-funding names."""
    n, syms = 200, ["A", "B", "C", "D"]
    fund = {"A": [0.002] * n, "B": [0.001] * n, "C": [-0.001] * n, "D": [-0.002] * n}
    p = _panel(_flatprices(n, syms), fund)
    w = S.xs_funding(p, lookback=10, k=1, rebal=1)
    last = w[-1]
    i = {s: p.symbols.index(s) for s in syms}
    assert last[i["A"]] < 0, "en yüksek funding short olmalı"
    assert last[i["D"]] > 0, "en düşük funding long olmalı"


def test_xs_funding_collects_the_spread_when_prices_do_not_move():
    n, syms = 300, ["A", "B", "C", "D"]
    fund = {"A": [0.002] * n, "B": [0.001] * n, "C": [-0.001] * n, "D": [-0.002] * n}
    p = _panel(_flatprices(n, syms), fund)
    w = S.xs_funding(p, lookback=10, k=1, rebal=1)
    r = engine.evaluate(w, p.ret, p.mask, cost=0.0, funding=p.funding)
    assert r[50:].sum() > 0, "sabit fiyatta yayılım pozitif olmalıydı"


def test_carry_short_is_short_the_market_not_neutral():
    """The control arm. It must show a NET short exposure, so that any success of
    the neutral version cannot be explained by 'it was short in a bear'."""
    n, syms = 200, ["A", "B", "C"]
    fund = {s: [0.001] * n for s in syms}
    p = _panel(_flatprices(n, syms), fund)
    w = S.carry_short(p, lookback=10, rebal=1)
    assert w[-1].sum() < -0.9, w[-1]


def test_funding_families_refuse_a_panel_without_funding():
    p = _panel(_flatprices(50, ["A", "B", "C", "D"]))
    for fn in (S.xs_funding, S.carry_short):
        try:
            fn(p)
        except ValueError:
            continue
        raise AssertionError(f"{fn.__name__} funding'siz panelde sessizce çalıştı")
