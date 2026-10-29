"""
Candidate Scoring — Ranks edge candidates by confidence and reliability.

Scoring factors:
    + Large sample size (n >= 50: +20, n >= 100: +30)
    + Positive expectancy (+10 base, +10 per 0.1R above zero)
    + Multiple symbols contributing (+15)
    + Profit factor > 1.5 (+10)
    + Win rate > 40% (+5)
    - Small sample (n < 30: -20)
    - Single event dependency (-15 per dependency)
    - High concentration (-10)
"""

from __future__ import annotations

from research_engine.edge_candidates.models import EdgeCandidate

_MIN_SAMPLE_PASS = 30
_MIN_SAMPLE_FLAG = 20


def score_candidate(candidate: EdgeCandidate) -> EdgeCandidate:
    """Score a candidate and assign confidence + overfit risk."""
    score = 50.0  # Base

    # Sample size
    n = candidate.sample_size
    if n >= 100:
        score += 30
    elif n >= 50:
        score += 20
    elif n >= 30:
        score += 10
    elif n < _MIN_SAMPLE_FLAG:
        score -= 20
        candidate.low_sample = True

    # Expectancy
    ev = candidate.expectancy
    if ev > 0:
        score += 10
        score += min(20, ev * 100)  # +10 per 0.1R, capped at +20
    else:
        score -= 30

    # Profit factor
    if candidate.profit_factor >= 2.0:
        score += 15
    elif candidate.profit_factor >= 1.5:
        score += 10
    elif candidate.profit_factor < 1.0:
        score -= 15

    # Win rate
    if candidate.win_rate >= 0.50:
        score += 10
    elif candidate.win_rate >= 0.40:
        score += 5
    elif candidate.win_rate < 0.25:
        score -= 10

    # Dependency penalties
    if candidate.single_pattern_dependent:
        score -= 15
    if candidate.single_symbol_dependent:
        score -= 15
    if candidate.single_regime_dependent:
        score -= 10

    # Clamp
    candidate.confidence_score = max(0.0, min(100.0, score))

    # Stability: based on sample + diversification
    stability = 50.0
    if n >= 50:
        stability += 20
    if not candidate.single_pattern_dependent:
        stability += 15
    if not candidate.single_symbol_dependent:
        stability += 15
    if candidate.low_sample:
        stability -= 30
    candidate.stability_score = max(0.0, min(100.0, stability))

    # Overfit risk
    if n < _MIN_SAMPLE_FLAG or (candidate.single_pattern_dependent and candidate.single_regime_dependent):
        candidate.overfit_risk = "HIGH"
    elif n < _MIN_SAMPLE_PASS or candidate.single_pattern_dependent:
        candidate.overfit_risk = "MEDIUM"
    else:
        candidate.overfit_risk = "LOW"

    return candidate
