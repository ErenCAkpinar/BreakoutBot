"""The statistics in experiments/wfa.py, checked against known values.

A selection-bias correction that is quietly wrong is worse than none: it puts a
number of authority on a conclusion nobody re-derives. Each function here is
pinned to something independently knowable — a closed form, a symmetry, or a
construction whose answer is obvious by design.
"""
from __future__ import annotations

import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "experiments"))

import wfa  # noqa: E402


# ── inverse normal CDF ───────────────────────────────────────────────────────

def test_inv_norm_matches_known_quantiles():
    for p, want in [(0.5, 0.0), (0.975, 1.959964), (0.95, 1.644854),
                    (0.99, 2.326348), (0.025, -1.959964)]:
        assert abs(wfa._inv_norm(p) - want) < 1e-4, f"p={p}"


def test_inv_norm_is_the_inverse_of_norm_cdf():
    for p in (0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99):
        assert abs(wfa._norm_cdf(wfa._inv_norm(p)) - p) < 1e-6


# ── Sharpe & moments ─────────────────────────────────────────────────────────

def test_sharpe_is_mean_over_sd():
    rs = [1.0, -1.0, 2.0, 0.0, -2.0, 3.0]
    assert abs(wfa.sharpe(rs) - statistics.fmean(rs) / statistics.pstdev(rs)) < 1e-12


def test_sharpe_of_a_constant_series_is_zero_not_infinite():
    """Zero dispersion must not divide by zero — a flat series is the degenerate
    case a live sleeve produces when it never trades."""
    assert wfa.sharpe([0.5] * 10) == 0.0


def test_moments_on_a_normal_sample():
    rnd = random.Random(11)
    rs = [rnd.gauss(0, 1) for _ in range(20000)]
    sk, ku = wfa._moments(rs)
    assert abs(sk) < 0.06, sk           # normal: skew 0
    assert abs(ku - 3.0) < 0.12, ku     # normal: kurtosis 3 (non-excess)


def test_moments_detects_right_skew():
    rnd = random.Random(3)
    rs = [rnd.expovariate(1.0) for _ in range(20000)]
    sk, _ = wfa._moments(rs)
    assert sk > 1.5, "exponential is strongly right-skewed"


# ── expected max Sharpe ──────────────────────────────────────────────────────

def test_expected_max_sharpe_grows_with_trials():
    """The whole point: trying more candidates raises the bar the winner must
    clear. If this were flat, the correction would do nothing."""
    v = 0.04
    vals = [wfa.expected_max_sharpe(n, v) for n in (2, 5, 23, 100)]
    assert all(b > a for a, b in zip(vals, vals[1:])), vals
    assert vals[0] > 0


def test_expected_max_sharpe_scales_with_dispersion():
    """Doubling the SD of the trials doubles the expected maximum."""
    a = wfa.expected_max_sharpe(23, 0.01)      # sd 0.1
    b = wfa.expected_max_sharpe(23, 0.04)      # sd 0.2
    assert abs(b - 2 * a) < 1e-9


def test_expected_max_sharpe_is_zero_without_trials_or_dispersion():
    assert wfa.expected_max_sharpe(1, 0.04) == 0.0
    assert wfa.expected_max_sharpe(23, 0.0) == 0.0


def test_expected_max_sharpe_beats_a_monte_carlo_sanity_check():
    """Against the empirical max of 23 zero-mean draws."""
    rnd = random.Random(5)
    sd = 0.2
    emp = statistics.fmean(
        [max(rnd.gauss(0, sd) for _ in range(23)) for _ in range(4000)])
    approx = wfa.expected_max_sharpe(23, sd ** 2)
    assert abs(approx - emp) < 0.06, (approx, emp)


# ── Deflated Sharpe ──────────────────────────────────────────────────────────

def test_dsr_falls_when_more_candidates_were_tried():
    """Same track record, more trials → less impressive. This is the correction
    the project has never applied."""
    rnd = random.Random(2)
    rs = [rnd.gauss(0.15, 1.0) for _ in range(400)]
    wfa._TRIAL_VAR[0] = 0.04
    few = wfa.deflated_sharpe(rs, n_trials=2)
    many = wfa.deflated_sharpe(rs, n_trials=200)
    assert many["sr_star"] > few["sr_star"]
    assert many["dsr"] < few["dsr"]


def test_dsr_is_high_for_a_strong_record_and_low_for_noise():
    rnd = random.Random(4)
    wfa._TRIAL_VAR[0] = 0.01
    strong = wfa.deflated_sharpe([rnd.gauss(0.5, 1.0) for _ in range(500)], 23)
    noise = wfa.deflated_sharpe([rnd.gauss(0.0, 1.0) for _ in range(500)], 23)
    assert strong["dsr"] > 0.95
    assert noise["dsr"] < 0.6


def test_dsr_declines_to_none_on_a_tiny_sample():
    assert wfa.deflated_sharpe([0.1, 0.2, 0.3], 23)["dsr"] is None


# ── PBO ──────────────────────────────────────────────────────────────────────

def _cand(series: dict[str, list[float]], span: int = 8) -> dict[str, list[dict]]:
    """Turn per-candidate R series into position records spread over time."""
    out = {}
    for sym, rs in series.items():
        n = len(rs)
        out[sym] = [{"ts_open": int(i * span * wfa.BAR_MS * 1000 / n),
                     "ts_close": int(i * span * wfa.BAR_MS * 1000 / n) + wfa.BAR_MS,
                     "pnl": r * 10.0, "exit_type": "TP2", "legs": ["TP2"]}
                    for i, r in enumerate(rs)]
    return out


def test_pbo_is_high_when_every_candidate_is_pure_noise():
    """Pure noise: whoever wins in-sample has no reason to win out-of-sample, so
    the in-sample winner should land below median about half the time."""
    rnd = random.Random(9)
    cand = _cand({f"C{i}": [rnd.gauss(0, 1) for _ in range(400)] for i in range(12)})
    assert wfa.pbo(cand, blocks=6)["pbo"] > 0.35


def test_pbo_is_low_when_one_candidate_genuinely_dominates():
    """A real, persistent edge must be recoverable — otherwise the metric would
    condemn every strategy and tell us nothing."""
    rnd = random.Random(10)
    series = {f"C{i}": [rnd.gauss(0, 1) for _ in range(400)] for i in range(12)}
    series["STAR"] = [rnd.gauss(1.2, 1.0) for _ in range(400)]
    assert wfa.pbo(_cand(series), blocks=6)["pbo"] < 0.15


def test_pbo_enumerates_the_full_balanced_partition_set():
    """C(8,4) = 70. A silent shortcut here would quietly change the estimate."""
    rnd = random.Random(1)
    cand = _cand({f"C{i}": [rnd.gauss(0, 1) for _ in range(200)] for i in range(6)})
    assert wfa.pbo(cand, blocks=8)["n_partitions"] == math.comb(8, 4)
