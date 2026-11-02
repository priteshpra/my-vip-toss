"""Rigorous statistical accuracy calculations, Wilson score intervals, and baseline tests.

Strict adherence to mathematical honesty:
- A coin toss is an independent Bernoulli trial (p = 0.50).
- Real measured accuracy only; never display inflated or guaranteed terms.
- Low sample sizes (n < 30) explicitly flagged.
- Wilson Score 95% confidence intervals and two-sided exact binomial tests against 50%.
"""

import math
from typing import Tuple, Dict, Any, List, Optional
from scipy.stats import binomtest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Prediction, PredictionOutcome
from app.schemas import AccuracyMetrics, WilsonConfidenceInterval, BinomialBaselineTest


def calculate_wilson_score_interval(
    wins: int,
    total: int,
    confidence: float = 0.95
) -> Tuple[float, float]:
    """
    Calculate the Wilson Score confidence interval for a binomial proportion.
    Returns (lower_bound, upper_bound) rounded to 4 decimals in range [0.0, 1.0].
    
    Formula:
    (p + z^2/(2n) +/- z * sqrt((p(1-p)/n) + (z^2/(4n^2)))) / (1 + z^2/n)
    """
    if total <= 0:
        return 0.0, 0.0

    # Critical value z: 1.95996 for 95% confidence
    z = 1.959963984540054 if abs(confidence - 0.95) < 0.01 else 1.6448536269514722
    p = wins / total

    denominator = 1.0 + (z**2) / total
    centre_adjusted_probability = p + (z**2) / (2 * total)
    adjusted_standard_deviation = math.sqrt((p * (1.0 - p) + (z**2) / (4.0 * total)) / total)

    lower = (centre_adjusted_probability - z * adjusted_standard_deviation) / denominator
    upper = (centre_adjusted_probability + z * adjusted_standard_deviation) / denominator

    return max(0.0, round(lower, 4)), min(1.0, round(upper, 4))


def binomial_test_against_baseline(
    wins: int,
    total: int,
    p0: float = 0.50
) -> Dict[str, Any]:
    """
    Perform a two-sided exact binomial test against the theoretical 50% fair coin baseline.
    Null Hypothesis H0: True toss winning probability p == 0.50.
    """
    if total <= 0:
        return {
            "sample_proportion": 0.50,
            "p_value": 1.0,
            "is_significant": False,
            "conclusion": "No trials evaluated yet."
        }

    res = binomtest(wins, total, p=p0, alternative="two-sided")
    p_val = float(res.pvalue)
    prop = wins / total
    is_sig = p_val < 0.05

    if is_sig:
        direction = "above" if prop > p0 else "below"
        conclusion = (
            f"Statistically significant divergence from 50% fair baseline (p={p_val:.4f} < 0.05, {direction} baseline)."
        )
    else:
        conclusion = (
            f"Statistically indistinguishable from a 50/50 fair coin flip (p={p_val:.4f} >= 0.05)."
        )

    return {
        "sample_proportion": round(prop, 4),
        "p_value": round(p_val, 4),
        "is_significant": is_sig,
        "conclusion": conclusion
    }


def calculate_brier_score(predictions: List[Tuple[float, int]]) -> Optional[float]:
    """
    Compute Brier score (mean squared error of probabilistic forecast vs binary outcome 0 or 1).
    Lower is better: 0.0 is perfect calibration, 0.25 is random guessing (predicting 0.50).
    """
    if not predictions:
        return None
    sq_errs = [(p - actual)**2 for p, actual in predictions]
    return round(sum(sq_errs) / len(sq_errs), 4)


async def get_accuracy_metrics_for_source(
    session: AsyncSession,
    source_channel: Optional[str] = None,
    model_version_id: Optional[str] = None,
) -> AccuracyMetrics:
    """
    Aggregate all evaluated pre-toss predictions and compute comprehensive honesty metrics.
    """
    query = (
        select(
            Prediction.lean,
            PredictionOutcome.result,
            Prediction.probability,
        )
        .join(PredictionOutcome, Prediction.id == PredictionOutcome.prediction_id)
    )

    if source_channel:
        query = query.where(Prediction.source == source_channel)

    result = await session.execute(query)
    rows = result.all()

    total_calls = len(rows)
    play_calls = 0
    skip_calls = 0
    wins = 0
    losses = 0
    brier_pairs: List[Tuple[float, int]] = []

    for lean, result_val, prob in rows:
        if lean == "NO_EDGE" or result_val == "SKIPPED":
            skip_calls += 1
            continue
        play_calls += 1
        if result_val == "PASS":
            wins += 1
            if prob is not None:
                brier_pairs.append((prob, 1))
        elif result_val == "FAIL":
            losses += 1
            if prob is not None:
                brier_pairs.append((prob, 0))

    evaluated_calls = wins + losses
    measured_accuracy_pct = round((wins / evaluated_calls * 100.0), 2) if evaluated_calls > 0 else 50.0

    # Wilson 95% Confidence Interval
    ci_lower, ci_upper = calculate_wilson_score_interval(wins, evaluated_calls, confidence=0.95)
    wilson_obj = WilsonConfidenceInterval(
        confidence_level=0.95,
        lower_bound=ci_lower,
        upper_bound=ci_upper,
        display_str=f"[{round(ci_lower * 100, 1)}% - {round(ci_upper * 100, 1)}%]"
    )

    # Exact binomial test vs 50% baseline
    binom_data = binomial_test_against_baseline(wins, evaluated_calls, p0=0.50)
    binom_obj = BinomialBaselineTest(
        baseline_probability=0.50,
        sample_proportion=binom_data["sample_proportion"],
        p_value=binom_data["p_value"],
        is_significant=binom_data["is_significant"],
        conclusion=binom_data["conclusion"]
    )

    # Brier Score
    brier = calculate_brier_score(brier_pairs)

    sample_status = "SUFFICIENT (n >= 30)" if evaluated_calls >= 30 else f"LOW SAMPLE (n = {evaluated_calls} < 30)"

    return AccuracyMetrics(
        source_name=source_channel or "All Tracked Sources Combined",
        total_calls=total_calls,
        play_calls=play_calls,
        skip_calls=skip_calls,
        evaluated_calls=evaluated_calls,
        wins=wins,
        losses=losses,
        measured_accuracy_pct=measured_accuracy_pct,
        wilson_interval_95=wilson_obj,
        binomial_test=binom_obj,
        brier_score=brier,
        sample_size_status=sample_status
    )

