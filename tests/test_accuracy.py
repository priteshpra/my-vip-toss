"""Unit tests for Wilson Score Intervals, Binomial Baseline Null Tests, and Brier Score."""

import pytest
from app.services.accuracy_service import (
    calculate_wilson_score_interval,
    binomial_test_against_baseline,
    calculate_brier_score
)


def test_wilson_interval_zero_trials():
    """Ensure zero trials returns (0.0, 0.0) safely."""
    lower, upper = calculate_wilson_score_interval(0, 0)
    assert lower == 0.0
    assert upper == 0.0


def test_wilson_interval_fair_coin():
    """Verify Wilson interval for 50 heads in 100 flips centered near 0.50."""
    lower, upper = calculate_wilson_score_interval(50, 100, 0.95)
    assert 0.0 <= lower <= 0.50
    assert 0.50 <= upper <= 1.0
    assert round(lower, 2) == 0.40
    assert round(upper, 2) == 0.60


def test_wilson_interval_bounds_never_exceed_unit():
    """Verify lower bound >= 0 and upper bound <= 1 for extreme outcomes."""
    lower_0, upper_0 = calculate_wilson_score_interval(0, 50, 0.95)
    assert lower_0 == 0.0
    assert 0.0 < upper_0 < 0.10

    lower_100, upper_100 = calculate_wilson_score_interval(50, 50, 0.95)
    assert 0.90 < lower_100 < 1.0
    assert upper_100 == 1.0


def test_wilson_interval_shrinks_with_sample_size():
    """Verify confidence interval width narrows as sample size n increases."""
    w1_low, w1_high = calculate_wilson_score_interval(50, 100)
    w2_low, w2_high = calculate_wilson_score_interval(500, 1000)
    width_100 = w1_high - w1_low
    width_1000 = w2_high - w2_low
    assert width_1000 < width_100


def test_binomial_test_null_hypothesis_not_rejected():
    """For 52 wins in 100 trials, fair coin null hypothesis must NOT be rejected (p >= 0.05)."""
    res = binomial_test_against_baseline(52, 100, p0=0.50)
    assert res["is_significant"] is False
    assert res["p_value"] > 0.05
    assert "indistinguishable from a 50/50 fair coin flip" in res["conclusion"].lower()


def test_binomial_test_null_hypothesis_rejected():
    """For 75 wins in 100 trials, deviation from 50% is statistically significant (p < 0.05)."""
    res = binomial_test_against_baseline(75, 100, p0=0.50)
    assert res["is_significant"] is True
    assert res["p_value"] < 0.001
    assert "Statistically significant divergence" in res["conclusion"]


def test_brier_score_calibration():
    """Verify Brier score computation: 0.0 is perfect, 0.25 is uninformative 50/50 forecast."""
    # Perfect forecast: predicted 1.0 and event happened, predicted 0.0 and did not happen
    perfect_preds = [(1.0, 1), (0.0, 0), (1.0, 1), (0.0, 0)]
    assert calculate_brier_score(perfect_preds) == 0.0

    # 50-50 guess: (0.50 - 1)^2 = 0.25, (0.50 - 0)^2 = 0.25
    coin_preds = [(0.50, 1), (0.50, 0), (0.50, 1), (0.50, 0)]
    assert calculate_brier_score(coin_preds) == 0.25

    # Empty list
    assert calculate_brier_score([]) is None
