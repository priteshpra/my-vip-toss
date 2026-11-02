"""Rigorous statistical accuracy calculations, Wilson score intervals, and baseline tests.

Strict adherence to mathematical honesty:
- A coin toss is an independent Bernoulli trial (p = 0.50).
- Real measured accuracy only; never display inflated or misleading terms.
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
from app.db import format_ist


def calculate_calibration_table(pairs: List[Tuple[float, int]]) -> List[Dict[str, Any]]:
    """
    Compute probability calibration table comparing predicted probability buckets vs empirical hit-rates.
    Buckets: 50-54%, 55-59%, 60-64%, 65-69%, 70%+
    """
    buckets = [
        {"range": (0.50, 0.55), "label": "50% - 54%", "predicted_probs": [], "actuals": []},
        {"range": (0.55, 0.60), "label": "55% - 59%", "predicted_probs": [], "actuals": []},
        {"range": (0.60, 0.65), "label": "60% - 64%", "predicted_probs": [], "actuals": []},
        {"range": (0.65, 0.70), "label": "65% - 69%", "predicted_probs": [], "actuals": []},
        {"range": (0.70, 1.01), "label": "70%+", "predicted_probs": [], "actuals": []},
    ]

    for prob, actual in pairs:
        placed = False
        for b in buckets:
            low, high = b["range"]
            if low <= prob < high or (high == 1.01 and prob >= 0.70):
                b["predicted_probs"].append(prob)
                b["actuals"].append(actual)
                placed = True
                break
        if not placed and buckets:
            # Default to first bucket if below 0.50
            buckets[0]["predicted_probs"].append(prob)
            buckets[0]["actuals"].append(actual)

    table = []
    for b in buckets:
        n = len(b["actuals"])
        if n > 0:
            avg_pred = round(sum(b["predicted_probs"]) / n * 100.0, 1)
            actual_hits = sum(b["actuals"])
            actual_rate = round(actual_hits / n * 100.0, 1)
            gap = round(actual_rate - avg_pred, 1)
            ci_l, ci_u = calculate_wilson_score_interval(actual_hits, n, 0.95)
            ci_str = f"[{round(ci_l * 100, 1)}% - {round(ci_u * 100, 1)}%]"
        else:
            avg_pred = None
            actual_rate = None
            gap = None
            ci_str = "N/A"

        table.append({
            "bucket": b["label"],
            "count": n,
            "mean_predicted_pct": avg_pred,
            "actual_win_rate_pct": actual_rate,
            "calibration_gap": gap,
            "ci_95": ci_str,
            "is_sufficient": n >= 30
        })

    return table


def calculate_streaks_from_results(results: List[str]) -> Dict[str, Any]:
    """
    Calculate maximum winning streak, maximum losing streak, and current active streak.
    Results list must be ordered chronologically (oldest to newest).
    """
    if not results:
        return {
            "max_win_streak": 0,
            "max_loss_streak": 0,
            "current_streak_count": 0,
            "current_streak_type": "NONE",
            "current_streak_display": "None"
        }

    max_win = 0
    max_loss = 0
    curr_win = 0
    curr_loss = 0

    for r in results:
        if r == "PASS":
            curr_win += 1
            curr_loss = 0
            if curr_win > max_win:
                max_win = curr_win
        elif r == "FAIL":
            curr_loss += 1
            curr_win = 0
            if curr_loss > max_loss:
                max_loss = curr_loss

    # Current streak from last item
    last_res = results[-1] if results else "NONE"
    curr_streak = 0
    if last_res in ("PASS", "FAIL"):
        for r in reversed(results):
            if r == last_res:
                curr_streak += 1
            else:
                break

    streak_type = "PASS" if last_res == "PASS" else ("FAIL" if last_res == "FAIL" else "NONE")
    streak_sign = f"+{curr_streak} (PASS)" if streak_type == "PASS" else (f"-{curr_streak} (FAIL)" if streak_type == "FAIL" else "0")

    return {
        "max_win_streak": max_win,
        "max_loss_streak": max_loss,
        "current_streak_count": curr_streak,
        "current_streak_type": streak_type,
        "current_streak_display": streak_sign
    }


def calculate_rolling_hit_rates(
    chronological_outcomes: List[Tuple[Any, str]],
    windows: List[int] = [30, 100]
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Compute rolling hit-rate series for windows (e.g. 30 and 100).
    chronological_outcomes: List of (date/time, result string "PASS"/"FAIL").
    """
    series_map: Dict[str, List[Dict[str, Any]]] = {}

    for w in windows:
        window_points = []
        if len(chronological_outcomes) >= w:
            for i in range(w, len(chronological_outcomes) + 1):
                slice_data = chronological_outcomes[i - w:i]
                wins = sum(1 for _, res in slice_data if res == "PASS")
                hit_rate = round(wins / w * 100.0, 1)
                t_stamp, _ = slice_data[-1]
                t_str = str(t_stamp)[:10] if t_stamp else f"Match {i}"
                window_points.append({
                    "match_index": i,
                    "date": t_str,
                    "hit_rate": hit_rate,
                    "window_size": w
                })
        series_map[f"rolling_{w}"] = window_points

    return series_map


async def get_accuracy_dashboard_data(
    session: AsyncSession,
    source_channel: Optional[str] = None
) -> Dict[str, Any]:
    """
    Aggregate all honesty and truth analytics for the Accuracy Dashboard:
    - Overall hit-rate with n, 95% Wilson CI, difference from 50% baseline
    - Binomial baseline p-value and significance
    - Probability calibration table and Brier score
    - Calls taken vs No edge skipped
    - Rolling 30 and 100 hit rates
    - Streaks
    - Prominent banner if indistinguishable from 50%
    """
    metrics = await get_accuracy_metrics_for_source(session, source_channel)

    # Fetch ordered individual outcomes for calibration, streaks, and rolling charts
    query = (
        select(
            Prediction.locked_at_utc,
            Prediction.lean,
            Prediction.probability,
            PredictionOutcome.result
        )
        .join(PredictionOutcome, Prediction.id == PredictionOutcome.prediction_id)
        .order_by(Prediction.locked_at_utc.asc())
    )
    if source_channel:
        query = query.where(Prediction.source == source_channel)

    res = await session.execute(query)
    rows = res.all()

    play_pairs: List[Tuple[float, int]] = []
    play_results_chronological: List[str] = []
    play_dated_outcomes: List[Tuple[Any, str]] = []

    for locked_at, lean, prob, result_val in rows:
        if lean == "NO_EDGE" or result_val == "SKIPPED":
            continue
        p = prob if prob is not None else 0.50
        if result_val == "PASS":
            play_pairs.append((p, 1))
            play_results_chronological.append("PASS")
            play_dated_outcomes.append((locked_at, "PASS"))
        elif result_val == "FAIL":
            play_pairs.append((p, 0))
            play_results_chronological.append("FAIL")
            play_dated_outcomes.append((locked_at, "FAIL"))

    # Calibration table
    cal_table = calculate_calibration_table(play_pairs)

    # Streaks
    streaks = calculate_streaks_from_results(play_results_chronological)

    # Rolling hit rates
    rolling_data = calculate_rolling_hit_rates(play_dated_outcomes, windows=[30, 100])

    # Baseline comparison metrics
    diff_from_baseline = round(metrics.measured_accuracy_pct - 50.0, 2)
    is_indistinguishable = (not metrics.binomial_test.is_significant) or (metrics.evaluated_calls < 30)

    if is_indistinguishable:
        banner_msg = "STATISTICALLY INDISTINGUISHABLE FROM 50% RANDOM CHANCE (p-value >= 0.05 or n < 30). There is no empirical edge over a fair coin flip."
    else:
        banner_msg = f"STATISTICALLY SIGNIFICANT DIVERGENCE FROM BASELINE (p = {metrics.binomial_test.p_value:.4f} < 0.05)."

    return {
        "metrics": metrics,
        "diff_from_baseline": diff_from_baseline,
        "calibration_table": cal_table,
        "streaks": streaks,
        "rolling_data": rolling_data,
        "is_indistinguishable_from_50": is_indistinguishable,
        "banner_message": banner_msg,
        "calls_taken": metrics.play_calls,
        "no_edge_skipped": metrics.skip_calls,
        "disclaimer": "Toss is a random event. Past stats do not guarantee results. Play responsibly."
    }


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

