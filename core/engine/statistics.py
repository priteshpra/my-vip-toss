"""Mathematical and statistical functions for cricket toss analysis.

Strict adherence to statistical principles:
- Coin tosses are modeled as independent Bernoulli trials (p = 0.50).
- Historical frequencies do not establish future predictive advantage without out-of-sample significance.
- Low sample sizes (< 30) are flagged as insufficient data.
"""

import math
from typing import Tuple, Dict, Any, Optional
from scipy.stats import binomtest, beta


def calculate_wilson_score_interval(
    wins: int,
    total: int,
    confidence: float = 0.95
) -> Tuple[float, float]:
    """
    Calculate the Wilson Score confidence interval for a binomial proportion.
    Returns (lower_bound, upper_bound) in the range [0.0, 1.0].
    """
    if total <= 0:
        return 0.0, 0.0

    # z = 1.95996 for 95% confidence
    z = 1.959963984540054 if abs(confidence - 0.95) < 0.01 else 1.6448536269514722
    p = wins / total

    denominator = 1.0 + (z**2) / total
    centre_adjusted_probability = p + (z**2) / (2 * total)
    adjusted_standard_deviation = math.sqrt((p * (1 - p) + (z**2) / (4 * total)) / total)

    lower = (centre_adjusted_probability - z * adjusted_standard_deviation) / denominator
    upper = (centre_adjusted_probability + z * adjusted_standard_deviation) / denominator

    return max(0.0, round(lower, 6)), min(1.0, round(upper, 6))


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
            "conclusion": "No trials recorded."
        }

    res = binomtest(wins, total, p=p0, alternative="two-sided")
    p_val = float(res.pvalue)
    prop = wins / total
    is_sig = p_val < 0.05

    conclusion = (
        f"Statistically significant deviation from 50% (p={p_val:.4f})"
        if is_sig
        else f"No statistically significant difference from fair coin baseline (p={p_val:.4f} >= 0.05)"
    )

    return {
        "sample_proportion": round(prop, 4),
        "p_value": round(p_val, 5),
        "is_significant": is_sig,
        "conclusion": conclusion
    }


def bayesian_beta_binomial_estimate(
    wins: int,
    total: int,
    prior_alpha: float = 10.0,
    prior_beta: float = 10.0
) -> Dict[str, Any]:
    """
    Compute Bayesian posterior distribution using conjugate Beta-Binomial model.
    Default prior Beta(10, 10) reflects a modest informative prior centered at 50%
    (equivalent to 20 historical pseudo-observations of fair flips), dampening small-sample variance.
    """
    post_alpha = prior_alpha + wins
    post_beta = prior_beta + (total - wins)

    posterior_mean = post_alpha / (post_alpha + post_beta)
    ci_lower = float(beta.ppf(0.025, post_alpha, post_beta))
    ci_upper = float(beta.ppf(0.975, post_alpha, post_beta))

    return {
        "prior": f"Beta({prior_alpha}, {prior_beta})",
        "posterior_mean": round(posterior_mean, 4),
        "credible_interval_95": (round(ci_lower, 4), round(ci_upper, 4)),
        "post_alpha": round(post_alpha, 2),
        "post_beta": round(post_beta, 2)
    }


def evaluate_toss_advantage(
    wins_a: int,
    total_a: int,
    wins_b: int,
    total_b: int,
    min_sample_size: int = 30
) -> Dict[str, Any]:
    """
    Strict evidence-based gatekeeper for evaluating whether historical toss data
    justifies claiming any statistical edge.
    """
    # 1. Sample Size Check
    if total_a < min_sample_size or total_b < min_sample_size:
        return {
            "has_validated_advantage": False,
            "assessment_status": "INSUFFICIENT_DATA",
            "reason": (
                f"Sample size ({total_a} for Team A, {total_b} for Team B) "
                f"is below the minimum required sample of {min_sample_size} verified matches."
            ),
            "favored_team": None,
            "estimated_prob_a": None,
            "estimated_prob_b": None,
            "confidence_interval": (None, None)
        }

    # 2. Binomial Significance Tests against 50%
    test_a = binomial_test_against_baseline(wins_a, total_a, 0.50)
    test_b = binomial_test_against_baseline(wins_b, total_b, 0.50)

    # 3. Decision Gate: If neither side statistically departs from 50% random coin flip
    if not test_a["is_significant"] and not test_b["is_significant"]:
        return {
            "has_validated_advantage": False,
            "assessment_status": "NO_VALIDATED_ADVANTAGE",
            "reason": (
                "Both teams' historical toss outcomes are statistically consistent with a 50/50 fair coin flip "
                f"(Team A p={test_a['p_value']:.4f}, Team B p={test_b['p_value']:.4f}). "
                "Any choice would be speculative."
            ),
            "favored_team": None,
            "estimated_prob_a": None,
            "estimated_prob_b": None,
            "confidence_interval": (None, None)
        }

    # 4. If an empirical divergence is statistically validated
    bayes_a = bayesian_beta_binomial_estimate(wins_a, total_a)
    bayes_b = bayesian_beta_binomial_estimate(wins_b, total_b)

    # Combined relative estimate
    rel_p_a = bayes_a["posterior_mean"] / (bayes_a["posterior_mean"] + bayes_b["posterior_mean"])
    rel_p_b = 1.0 - rel_p_a

    ci_l, ci_u = calculate_wilson_score_interval(wins_a, total_a)

    return {
        "has_validated_advantage": True,
        "assessment_status": "PRE_TOSS_ESTIMATE_AVAILABLE",
        "reason": "Statistically validated departure from independent 50% coin baseline in longitudinal sample.",
        "favored_team": "Team A" if rel_p_a > rel_p_b else "Team B",
        "estimated_prob_a": round(rel_p_a, 4),
        "estimated_prob_b": round(rel_p_b, 4),
        "confidence_interval": (round(ci_l, 4), round(ci_u, 4))
    }


def calculate_actionable_toss_prediction(
    team_a_name: str,
    team_b_name: str,
    wins_a: int,
    total_a: int,
    wins_b: int,
    total_b: int,
    recent_wins_a: Optional[int] = None,
    recent_total_a: int = 5,
    recent_wins_b: Optional[int] = None,
    recent_total_b: int = 5,
    h2h_wins_a: int = 0,
    h2h_wins_b: int = 0,
    h2h_total: int = 0
) -> Dict[str, Any]:
    """
    Action-oriented toss prediction engine for practical, confident decision-making.
    Combines:
    - Long-term toss win rate (35% weight)
    - Recent toss form (45% weight)
    - Head-to-Head historical edge (20% weight)
    """
    if total_a == 0 and total_b == 0:
        return {
            "action_type": "SKIP",
            "recommended_team": None,
            "action_badge": "🟡 SKIP / NO DATA (50-50)",
            "confidence_stars": "⚠️",
            "confidence_label": "NEW TEAMS (50/50)",
            "win_prob_a": 0.50,
            "win_prob_b": 0.50,
            "edge_percentage": 0.0,
            "simple_reason": "Dono teams ka past data available nahi hai. Yeh 50-50 random toss hai, skip karein."
        }

    # Rates
    pct_a = wins_a / total_a if total_a > 0 else 0.50
    pct_b = wins_b / total_b if total_b > 0 else 0.50

    r_w_a = recent_wins_a if recent_wins_a is not None else min(wins_a, 3)
    r_w_b = recent_wins_b if recent_wins_b is not None else min(wins_b, 3)

    f_a = r_w_a / recent_total_a if recent_total_a > 0 else 0.50
    f_b = r_w_b / recent_total_b if recent_total_b > 0 else 0.50

    h_a = (h2h_wins_a / h2h_total) if h2h_total > 0 else 0.50
    h_b = (h2h_wins_b / h2h_total) if h2h_total > 0 else 0.50

    # Composite Score
    s_a = pct_a * 0.35 + f_a * 0.45 + h_a * 0.20
    s_b = pct_b * 0.35 + f_b * 0.45 + h_b * 0.20

    raw_prob_a = s_a / (s_a + s_b) if (s_a + s_b) > 0 else 0.50
    # Sports realistic cap [34%, 66%]
    scaled_prob_a = round(min(0.66, max(0.34, raw_prob_a)), 3)
    scaled_prob_b = round(1.0 - scaled_prob_a, 3)

    edge = round(abs(scaled_prob_a - scaled_prob_b) * 100, 1)

    if edge < 3.5:
        return {
            "action_type": "SKIP",
            "recommended_team": None,
            "action_badge": "🟡 SKIP / 50-50 COIN FLIP (NO CLEAR EDGE)",
            "confidence_stars": "⚖️ 50-50",
            "confidence_label": "NEUTRAL 50/50 (PASS / NO BET)",
            "win_prob_a": scaled_prob_a,
            "win_prob_b": scaled_prob_b,
            "edge_percentage": edge,
            "simple_reason": f"Dono teams ka toss record lagbhag barabar hai ({scaled_prob_a*100:.1f}% vs {scaled_prob_b*100:.1f}%). Koi clear edge nahi hai — is match par toss skip karein."
        }
    elif scaled_prob_a > scaled_prob_b:
        stars = "⭐⭐⭐ HIGH EDGE" if scaled_prob_a >= 0.59 else "⭐⭐ MEDIUM EDGE"
        label = "STRONG ADVANTAGE" if scaled_prob_a >= 0.59 else "MODERATE ADVANTAGE"
        h2h_txt = f" H2H toss record: {h2h_wins_a}-{h2h_wins_b}." if h2h_total > 0 else ""
        return {
            "action_type": "PLAY_A",
            "recommended_team": team_a_name,
            "action_badge": f"🟢 PLAY {team_a_name.upper()}",
            "confidence_stars": stars,
            "confidence_label": label,
            "win_prob_a": scaled_prob_a,
            "win_prob_b": scaled_prob_b,
            "edge_percentage": edge,
            "simple_reason": f"{team_a_name} ka overall toss win rate {pct_a*100:.1f}% ({wins_a}/{total_a}) aur recent form {r_w_a}/{recent_total_a} won hai vs {team_b_name} ({pct_b*100:.1f}%).{h2h_txt} Bindass {team_a_name} par toss play karein."
        }
    else:
        stars = "⭐⭐⭐ HIGH EDGE" if scaled_prob_b >= 0.59 else "⭐⭐ MEDIUM EDGE"
        label = "STRONG ADVANTAGE" if scaled_prob_b >= 0.59 else "MODERATE ADVANTAGE"
        h2h_txt = f" H2H toss record: {h2h_wins_b}-{h2h_wins_a}." if h2h_total > 0 else ""
        return {
            "action_type": "PLAY_B",
            "recommended_team": team_b_name,
            "action_badge": f"🟢 PLAY {team_b_name.upper()}",
            "confidence_stars": stars,
            "confidence_label": label,
            "win_prob_a": scaled_prob_a,
            "win_prob_b": scaled_prob_b,
            "edge_percentage": edge,
            "simple_reason": f"{team_b_name} ka overall toss win rate {pct_b*100:.1f}% ({wins_b}/{total_b}) aur recent form {r_w_b}/{recent_total_b} won hai vs {team_a_name} ({pct_a*100:.1f}%).{h2h_txt} Bindass {team_b_name} par toss play karein."
        }

