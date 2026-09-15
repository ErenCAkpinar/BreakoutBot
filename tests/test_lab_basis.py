"""The basis trade: delta neutrality, the doubled cost, and the carry identity.

This is the only family in the lab whose appeal is that it does NOT take a view,
so the tests are about the construction rather than any signal. If the legs do
not actually cancel, or the cost of moving two of them is charged as one, the
result stops being a carry trade and becomes a directional bet wearing its name.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "experiments", "lab"))

import basis  # noqa: E402


def _bp(spot: dict, perp: dict, fund: dict) -> basis.BasisPanel:
    syms = sorted(spot)
    n = len(next(iter(spot.values())))
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    m = lambda d: np.column_stack([np.array(d[s], float) for s in syms])  # noqa: E731
    return basis.BasisPanel(syms, idx, m(spot), m(perp), m(fund))


# ── the construction ─────────────────────────────────────────────────────────

def test_perfectly_tracking_legs_leave_only_the_funding():
    """The identity the whole trade rests on: if spot and perp move together,
    the price terms cancel and the return IS the funding rate."""
    n = 50
    path = list(100 * np.cumprod(1 + np.random.default_rng(0).normal(0, 0.02, n)))
    bp = _bp({"A": path}, {"A": path}, {"A": [0.001] * n})
    w = np.ones((n, 1))
    r = basis.evaluate(bp, w, cost_per_side=0.0)
    assert np.allclose(r[1:], 0.001), r[:5]


def test_a_violent_price_move_does_not_reach_the_return():
    """Spot doubling would dominate any directional book. Here it must not
    matter at all, because the short perp doubles against it."""
    n = 30
    path = [100.0] * 15 + [200.0] * 15          # a 100% jump mid-sample
    bp = _bp({"A": path}, {"A": path}, {"A": [0.0] * n})
    r = basis.evaluate(bp, np.ones((n, 1)), cost_per_side=0.0)
    assert np.allclose(r, 0.0), "fiyat hareketi getiriye sızdı"


def test_basis_drift_is_what_remains_when_the_legs_diverge():
    """The residual risk that IS real: the two prices drifting apart."""
    n = 20
    spot = [100.0] * n
    perp = [100.0] * 10 + [101.0] * 10          # perp richens 1%
    bp = _bp({"A": spot}, {"A": perp}, {"A": [0.0] * n})
    r = basis.evaluate(bp, np.ones((n, 1)), cost_per_side=0.0)
    # Short the perp while it richens → a loss of that 1%.
    assert np.isclose(r.sum(), -0.01, atol=1e-9), r.sum()


# ── the cost ─────────────────────────────────────────────────────────────────

def test_opening_and_closing_charges_four_crossings_not_two():
    """Buy spot + short perp, then unwind both. Charging one leg would halve the
    cost of the single decision that governs this strategy."""
    n = 3
    flat = [100.0] * n
    bp = _bp({"A": flat}, {"A": flat}, {"A": [0.0] * n})
    w = np.array([[1.0], [0.0], [0.0]])
    r = basis.evaluate(bp, w, cost_per_side=0.001)
    assert np.isclose(r.sum(), -0.004), r        # 2 legs x 2 crossings x 0.001


def test_holding_without_rotation_costs_nothing_further():
    n = 10
    flat = [100.0] * n
    bp = _bp({"A": flat}, {"A": flat}, {"A": [0.0] * n})
    r = basis.evaluate(bp, np.ones((n, 1)), cost_per_side=0.001)
    assert np.isclose(r.sum(), -0.002)           # entry only, both legs


# ── selection ────────────────────────────────────────────────────────────────

def test_it_holds_the_highest_funding_coins():
    n = 300
    flat = [100.0] * n
    spot = {s: flat for s in "ABCD"}
    fund = {"A": [0.003] * n, "B": [0.002] * n, "C": [0.001] * n, "D": [-0.001] * n}
    bp = _bp(spot, dict(spot), fund)
    w = basis.weights(bp, top=2, lookback=10, rebal=1)
    i = {s: bp.symbols.index(s) for s in "ABCD"}
    assert w[-1][i["A"]] > 0 and w[-1][i["B"]] > 0
    assert w[-1][i["C"]] == 0 and w[-1][i["D"]] == 0


def test_min_funding_refuses_a_coin_that_would_pay_to_be_held():
    """A basis position on negative funding costs money to keep open. Five of 23
    coins ran negative over the last two years, so this is not hypothetical."""
    n = 200
    flat = [100.0] * n
    spot = {s: flat for s in "AB"}
    fund = {"A": [-0.001] * n, "B": [-0.002] * n}
    bp = _bp(spot, dict(spot), fund)
    w = basis.weights(bp, top=2, lookback=10, rebal=1, min_funding=0.0)
    assert np.allclose(w, 0.0), "negatif funding'e rağmen pozisyon açıldı"


def test_weights_are_held_between_rebalances():
    n = 120
    flat = [100.0] * n
    spot = {s: flat for s in "ABC"}
    fund = {"A": [0.003] * n, "B": [0.002] * n, "C": [0.001] * n}
    bp = _bp(spot, dict(spot), fund)
    w = basis.weights(bp, top=1, lookback=10, rebal=24)
    changes = np.abs(np.diff(w, axis=0)).sum(axis=1)
    assert (changes > 1e-12).sum() <= n // 24 + 2


def test_book_is_normalised_so_configs_stay_comparable():
    n = 200
    flat = [100.0] * n
    spot = {s: flat for s in "ABCDE"}
    fund = {s: [0.001 * (i + 1)] * n for i, s in enumerate("ABCDE")}
    bp = _bp(spot, dict(spot), fund)
    for top in (1, 3, 5):
        w = basis.weights(bp, top=top, lookback=10, rebal=1)
        assert np.isclose(w[-1].sum(), 1.0), (top, w[-1].sum())



def _mk(spot, perp, fund):
    import basis
    idx = pd.date_range("2024-01-01", periods=len(spot), freq="4h", tz="UTC")
    return basis.BasisPanel([f"C{i}" for i in range(spot.shape[1])], idx, spot, perp, fund)


def test_ledger_fixed_quantity_hedge_earns_nothing_from_price():
    """Review 2026-09-14 finding 3: spot 100→200→100 vs perp 101→201→101 with
    zero funding and zero cost — a fixed-quantity hedge has zero P&L; the old
    constant-dollar model produced +0.739%."""
    import basis
    import basis_ledger
    spot = np.array([[100.0], [200.0], [100.0]])
    perp = np.array([[101.0], [201.0], [101.0]])
    bp = _mk(spot, perp, np.zeros((3, 1)))
    w = np.ones((3, 1))
    led = basis_ledger.run(bp, w, cost_per_side=0.0)
    assert abs(np.prod(1 + led["r"]) - 1.0) < 1e-12
    old = float(np.prod(1 + basis.evaluate(bp, w, cost_per_side=0.0)[1:]) - 1)
    assert abs(old - 0.00739) < 1e-4          # the old model's artefact, for the record


def test_ledger_funding_accrues_on_perp_notional_and_transition_is_charged():
    import basis_ledger
    spot = np.full((4, 2), 100.0)
    perp = np.full((4, 2), 100.0)
    fund = np.zeros((4, 2))
    fund[1:, 0] = 0.001                                   # coin 0 pays 0.1%/bar
    bp = _mk(spot, perp, fund)
    w = np.zeros((4, 2))
    w[:2, 0] = 1.0
    w[2:, 1] = 1.0                                        # switch A → B at bar 2
    led = basis_ledger.run(bp, w, cost_per_side=0.001)
    assert abs(led["funding"][1] - 0.001) < 1e-12        # q·P·f, q sized from pre-cost equity $1
    assert abs(led["cost"][0] - 0.002) < 1e-9             # enter A: 2 legs × 0.001 × $1
    assert led["cost"][2] > led["cost"][0] * 1.9          # exit A + enter B: 4 legs
    assert led["cost"][1] == 0.0 and led["cost"][3] == 0.0
