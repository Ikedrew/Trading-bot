"""
Filter Hit Classifier — Diagnostic category classification for rejections.

Translates rejection outcomes into diagnostic filter categories.
Does NOT own the filter hit storage — only answers:
"What diagnostic bucket does this rejection belong to?"

This module OWNS:
    - New engine reason → filter key mapping
    - Old pipeline stage → filter key mapping
    - Reason-based fallback classification

This module does NOT own:
    - Filter hit storage (_filter_hits dict)
    - Decision flow
    - Trade decisions
    - Decision ledger
    - Runtime orchestration

Design: pure classification — stateless, never raises, returns FilterHitResult.
"""

from __future__ import annotations

from dataclasses import dataclass


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FilterHitResult:
    """Classification result for a rejection."""
    filter_key: str
    """Key into _filter_hits dict (e.g. 'market_context', 'score_reject')."""


# ─── STAGE → FILTER KEY MAPPING (old pipeline) ───────────────────────────────

_STAGE_TO_FILTER: dict[str, str] = {
    "market_context": "market_context",
    "structure_analysis": "structure/bias",
    "confirmations": "confirmation",
    "trade_quality": "trade_quality",
    "scoring_engine": "score_reject",
    "htf_constraint": "score_reject",
    "stability_gate": "stability_gate",
    "build_intent": "trade_quality",
}


# ─── CLASSIFICATION FUNCTIONS ─────────────────────────────────────────────────

def classify_new_engine_reason(reason: str) -> FilterHitResult:
    """
    Classify a new engine NO_TRADE reason into a filter hit category.

    Preserves exact logic from original inline classification in live_scanner.py.

    Args:
        reason: The rejection reason string from new engine result.

    Returns:
        FilterHitResult with the appropriate filter_key.
    """
    if "strategy_activation_failed" in reason:
        return FilterHitResult(filter_key="structure/bias")
    elif "score_below_threshold" in reason:
        return FilterHitResult(filter_key="score_reject")
    elif "ev_policy_blocked" in reason:
        if "NEGATIVE_EXPECTED_VALUE" in reason:
            return FilterHitResult(filter_key="score_reject")
        elif "RR_BELOW" in reason:
            return FilterHitResult(filter_key="trade_quality")
        else:
            return FilterHitResult(filter_key="score_reject")
    elif "policy_blocked" in reason:
        if "CHOP" in reason:
            return FilterHitResult(filter_key="market_context")
        elif "NEUTRAL_SCORE" in reason:
            return FilterHitResult(filter_key="score_reject")
        elif "CONFIDENCE" in reason:
            return FilterHitResult(filter_key="structure/bias")
        else:
            return FilterHitResult(filter_key="market_context")
    elif "swing_blocked" in reason:
        return FilterHitResult(filter_key="trend_filter")
    elif "risk_rejected" in reason:
        return FilterHitResult(filter_key="trade_quality")
    elif "data_invalid" in reason:
        return FilterHitResult(filter_key="market_context")
    elif "no_viable_pattern" in reason:
        return FilterHitResult(filter_key="pattern")
    else:
        return FilterHitResult(filter_key="market_context")


def classify_old_pipeline_drop(stage: str, reason: str) -> FilterHitResult | None:
    """
    Classify an old pipeline drop stage/reason into a filter hit category.

    Preserves exact logic from original inline classification in live_scanner.py.

    Args:
        stage: The pipeline stage where the drop occurred.
        reason: The rejection reason string.

    Returns:
        FilterHitResult with the filter_key, or None if no classification matched.
    """
    # First: check stage mapping
    _filter_key = _STAGE_TO_FILTER.get(stage)
    if _filter_key:
        return FilterHitResult(filter_key=_filter_key)

    # Fallback: classify by reason content
    if "pattern" in reason:
        return FilterHitResult(filter_key="pattern")
    elif "chop" in reason:
        return FilterHitResult(filter_key="chop_filter")
    elif "trend" in reason:
        return FilterHitResult(filter_key="trend_filter")
    elif "bias" in reason:
        return FilterHitResult(filter_key="structure/bias")

    # No match — return None (caller decides behaviour)
    return None
