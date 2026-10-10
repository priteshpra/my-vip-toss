"""Unit tests for statistical baseline, Wilson score, and decision gatekeeper."""

import pytest
from core.engine.statistics import (
    calculate_wilson_score_interval,
    binomial_test_against_baseline,
    bayesian_beta_binomial_estimate,
    evaluate_toss_advantage
)


def test_wilson_score_interval():
    # 50 wins out of 100 trials -> 50%
    lower, upper = calculate_wilson_score_interval(50, 100, confidence=0.95)
    assert lower < 0.50
    assert upper > 0.50
    assert 0.40 < lower < 0.45
    assert 0.55 < upper < 0.60

    # 0 total trials returns 0.0, 0.0
    l0, u0 = calculate_wilson_score_interval(0, 0)
    assert l0 == 0.0 and u0 == 0.0

    # 100% wins: lower bound must be > 0.90
    l_100, u_100 = calculate_wilson_score_interval(10, 10)
    assert l_100 > 0.65
    assert u_100 == 1.0


def test_binomial_test_against_baseline():
    # 16 wins out of 30 trials (53.3%) is NOT statistically significant from 50%
    res_fair = binomial_test_against_baseline(16, 30, p0=0.50)
    assert res_fair["is_significant"] is False
    assert res_fair["p_value"] > 0.05

    # 26 wins out of 30 trials (86.7%) IS statistically significant
    res_skewed = binomial_test_against_baseline(26, 30, p0=0.50)
    assert res_skewed["is_significant"] is True
    assert res_skewed["p_value"] < 0.001


def test_bayesian_beta_binomial_estimate():
    # Beta(10, 10) prior dampens small-sample variance
    # 2 wins out of 2 trials would naively be 100%, but Bayes posterior mean is (10+2)/(20+2) = 12/22 = 54.5%
    bayes = bayesian_beta_binomial_estimate(2, 2, prior_alpha=10.0, prior_beta=10.0)
    assert abs(bayes["posterior_mean"] - 0.5455) < 0.01
    assert bayes["credible_interval_95"][0] < bayes["posterior_mean"] < bayes["credible_interval_95"][1]


def test_evaluate_toss_advantage_decision_gate():
    # Scenario 1: Low sample size -> INSUFFICIENT_DATA
    res_low = evaluate_toss_advantage(wins_a=5, total_a=10, wins_b=6, total_b=12, min_sample_size=30)
    assert res_low["assessment_status"] == "INSUFFICIENT_DATA"
    assert res_low["has_validated_advantage"] is False

    # Scenario 2: Adequate sample (N=35), but neither side departs from 50% -> NO_VALIDATED_ADVANTAGE
    res_fair = evaluate_toss_advantage(wins_a=18, total_a=35, wins_b=17, total_b=35, min_sample_size=30)
    assert res_fair["assessment_status"] == "NO_VALIDATED_ADVANTAGE"
    assert res_fair["has_validated_advantage"] is False
    assert "No statistically significant difference" in res_fair["reason"] or "statistically consistent" in res_fair["reason"]
