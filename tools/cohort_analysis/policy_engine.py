"""
Policy Recommendation Engine — Classifies trade management policy from cohort expectancy.

STRICTLY OFFLINE — never imported by runtime code.
Pure analytical recommendation. Does NOT affect execution.

Policies:
  EXPAND   — Let runners run (trailing / extended RR targets)
  STANDARD — Baseline 2R behaviour
  REDUCE   — Tight TP / faster exit
  AVOID    — Negative expectancy cohort (warning)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tools.cohort_analysis.expectancy_model import CohortKey, CohortStats

Policy = Literal["EXPAND", "STANDARD", "REDUCE", "AVOID"]

# ─── THRESHOLDS ───────────────────────────────────────────────────────────────

_EXPAND_THRESHOLD = 0.8    # expectancy > 0.8R → let winners run
_STANDARD_THRESHOLD = 0.3  # 0.3R to 0.8R → baseline behaviour
_REDUCE_THRESHOLD = 0.0    # 0R to 0.3R → tighten exits
# Below 0R → AVOID


# ─── POLICY CLASSIFICATION ────────────────────────────────────────────────────

def classify_policy(stats: CohortStats) -> Policy:
    """
    Classify trade management policy from cohort performance statistics.

    Args:
        stats: CohortStats with expectancy, win_rate, variance, trade_count.

    Returns:
        Policy string: "EXPAND", "STANDARD", "REDUCE", or "AVOID".
    """
    if stats.trade_count < 3:
        return "STANDARD"  # Insufficient data — default to baseline

    if stats.expectancy > _EXPAND_THRESHOLD:
        return "EXPAND"
    elif stats.expectancy > _STANDARD_THRESHOLD:
        return "STANDARD"
    elif stats.expectancy >= _REDUCE_THRESHOLD:
        return "REDUCE"
    else:
        return "AVOID"


# ─── POLICY EXPLANATION ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class PolicyRecommendation:
    """Human-readable policy recommendation with reasoning."""

    policy: Policy
    expectancy: float
    reasoning: str
    trade_count: int
    confidence: str  # "HIGH" / "MEDIUM" / "LOW"


def explain_policy(stats: CohortStats, cohort: CohortKey | None = None) -> PolicyRecommendation:
    """
    Produce a human-readable policy recommendation with reasoning.

    Args:
        stats: CohortStats for the cohort.
        cohort: Optional CohortKey for context in explanation.

    Returns:
        PolicyRecommendation with policy, reasoning, and confidence.
    """
    policy = classify_policy(stats)

    # Confidence based on sample size
    if stats.trade_count >= 20:
        confidence = "HIGH"
    elif stats.trade_count >= 10:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # Build context label
    label = ""
    if cohort:
        label = f"{cohort.confirmation_strength}+{cohort.entry_timing}+{cohort.market_regime}"

    # Reasoning per policy
    if policy == "EXPAND":
        reasoning = (
            f"Expectancy {stats.expectancy:.3f}R exceeds {_EXPAND_THRESHOLD}R threshold. "
            f"Win rate {stats.win_rate:.0%} with {stats.trade_count} trades. "
            f"Recommend extended targets / trailing stops to capture full move."
        )
    elif policy == "STANDARD":
        if stats.trade_count < 3:
            reasoning = (
                f"Insufficient data ({stats.trade_count} trades). "
                f"Defaulting to STANDARD 2R baseline until more observations available."
            )
        else:
            reasoning = (
                f"Expectancy {stats.expectancy:.3f}R within standard range "
                f"({_STANDARD_THRESHOLD}–{_EXPAND_THRESHOLD}R). "
                f"Win rate {stats.win_rate:.0%}. Baseline 2R target appropriate."
            )
    elif policy == "REDUCE":
        reasoning = (
            f"Expectancy {stats.expectancy:.3f}R is marginal (0–{_STANDARD_THRESHOLD}R). "
            f"Win rate {stats.win_rate:.0%} with variance {stats.variance:.3f}. "
            f"Recommend tighter targets (1.5R) or faster exit to protect capital."
        )
    else:  # AVOID
        reasoning = (
            f"Expectancy {stats.expectancy:.3f}R is NEGATIVE. "
            f"Win rate {stats.win_rate:.0%} over {stats.trade_count} trades. "
            f"This cohort destroys capital. Consider filtering these setups entirely."
        )

    if label:
        reasoning = f"[{label}] {reasoning}"

    return PolicyRecommendation(
        policy=policy,
        expectancy=stats.expectancy,
        reasoning=reasoning,
        trade_count=stats.trade_count,
        confidence=confidence,
    )
