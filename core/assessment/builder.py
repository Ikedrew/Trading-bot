"""
Assessment Builder — Constructs Assessment records from engine evaluation results.

Called after run_new_engine() completes evaluation (regardless of EXECUTE/NO_TRADE outcome).
Extracts all assessment-relevant fields from the engine result dict.

This module is PURELY OBSERVATIONAL. It does NOT:
    - Affect trading decisions
    - Modify the pipeline
    - Block or gate execution
    - Change scoring or risk behaviour
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.assessment.assessment import Assessment, SCHEMA_VERSION, DATASET_VERSION


def build_assessment(
    *,
    engine_result: dict[str, Any],
    symbol: str,
    cycle_id: int,
    bar_time: int,
    bid: float = 0.0,
    ask: float = 0.0,
    runtime_session_id: str = "",
) -> Assessment | None:
    """
    Build an Assessment record from engine evaluation output.

    Called once per symbol per cycle after run_new_engine() returns.
    Only produces an Assessment if the engine reached the scoring stage
    (i.e., a pattern was detected and evaluated).

    Args:
        engine_result: Full dict returned by run_new_engine()
        symbol: Trading pair
        cycle_id: Current scan cycle
        bar_time: Unix seconds of the evaluated bar
        bid: Live bid price at assessment time
        ask: Live ask price at assessment time
        runtime_session_id: Bot runtime session identifier

    Returns:
        Assessment record, or None if engine did not reach scoring stage.
    """
    # Only build assessment if engine produced scoring data
    components = engine_result.get("components")
    if not components:
        return None  # Engine exited before scoring (no_viable_pattern, etc.)

    now = datetime.now(timezone.utc)
    assessed_at_utc = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    pattern = engine_result.get("pattern", "") or ""
    direction = engine_result.get("side", "") or ""

    # Build unique assessment ID
    assessment_id = f"{symbol}_{bar_time}_{pattern}_assessment"
    opportunity_id = f"{symbol}_{bar_time}_{pattern}"

    # Extract scoring
    score_neutral = float(engine_result.get("score_neutral", 0.0) or 0.0)
    score_strategy = float(engine_result.get("score_strategy", 0.0) or 0.0)
    score_delta = float(engine_result.get("delta", 0.0) or 0.0)

    # Extract strategy classification
    selected_strategy = str(engine_result.get("strategy", "") or "")
    strategy_confidence = float(engine_result.get("strategy_confidence", 0.0) or 0.0)
    regime = str(engine_result.get("activation_regime", "") or "")
    regime_confidence = float(engine_result.get("activation_regime_confidence", 0.0) or 0.0)
    weights_used = str(engine_result.get("weights_used", "") or "")

    # Extract probability
    p_success = float(engine_result.get("p_success", 0.0) or 0.0)
    probability_source = str(engine_result.get("probability_source", "") or "")
    probability_model_version = str(engine_result.get("probability_model_version", "") or "")

    # Extract EV
    ev = float(engine_result.get("ev", 0.0) or 0.0)
    ev_positive = bool(engine_result.get("ev_positive", False))
    ev_reward = float(engine_result.get("ev_reward", 0.0) or 0.0)
    ev_risk = float(engine_result.get("ev_risk", 0.0) or 0.0)
    rr_effective = float(engine_result.get("rr_effective", 0.0) or 0.0)

    # Extract market state
    market_state = str(engine_result.get("market_state", "") or "")
    market_state_confidence = float(engine_result.get("market_state_confidence", 0.0) or 0.0)

    # Extract uncertainty
    _uncertainty = engine_result.get("uncertainty")
    uncertainty_score = 0.0
    confidence_modifier = 0.0
    if _uncertainty is not None:
        uncertainty_score = float(getattr(_uncertainty, "uncertainty_score", 0.0) or 0.0)
        confidence_modifier = float(getattr(_uncertainty, "confidence_modifier", 0.0) or 0.0)

    # Extract confirmation
    confirmation_score = float(engine_result.get("confirmation_score", 0.0) or 0.0)
    confirmation_strength = str(engine_result.get("confirmation_strength", "") or "")

    # Extract reasoning
    _reasoning = engine_result.get("reasoning")
    reasoning_narrative = ""
    if _reasoning is not None:
        reasoning_narrative = str(getattr(_reasoning, "narrative", "") or "")
    policy_reasoning = str(engine_result.get("policy_reasoning", "") or "")

    # Extract evidence attribution
    _attribution = engine_result.get("attribution")
    evidence_contributions: list[dict[str, Any]] = []
    if _attribution is not None:
        _contribs = getattr(_attribution, "contributions", None)
        if _contribs:
            for c in _contribs:
                if hasattr(c, "to_dict"):
                    evidence_contributions.append(c.to_dict())
                elif isinstance(c, dict):
                    evidence_contributions.append(c)

    # Extract join keys
    entity_id = str(engine_result.get("entity_id", "") or "")
    correlation_id = str(engine_result.get("correlation_id", "") or "")

    return Assessment(
        # Identity
        assessment_id=assessment_id,
        opportunity_id=opportunity_id,
        symbol=symbol,
        cycle_id=cycle_id,
        bar_time=bar_time,
        # Scoring
        components=dict(components),
        score_neutral=round(score_neutral, 4),
        score_strategy=round(score_strategy, 4),
        score_delta=round(score_delta, 4),
        # Strategy
        pattern=pattern,
        direction=direction,
        selected_strategy=selected_strategy,
        strategy_confidence=round(strategy_confidence, 4),
        regime=regime,
        regime_confidence=round(regime_confidence, 4),
        weights_used=weights_used,
        # Probability
        p_success=round(p_success, 4),
        probability_source=probability_source,
        probability_model_version=probability_model_version,
        # EV
        ev=round(ev, 6),
        ev_positive=ev_positive,
        ev_reward=round(ev_reward, 4),
        ev_risk=round(ev_risk, 4),
        rr_effective=round(rr_effective, 4),
        # Market state
        market_state=market_state,
        market_state_confidence=round(market_state_confidence, 4),
        # Uncertainty
        uncertainty_score=round(uncertainty_score, 4),
        confidence_modifier=round(confidence_modifier, 4),
        # Confirmation
        confirmation_score=round(confirmation_score, 4),
        confirmation_strength=confirmation_strength,
        # Reasoning
        reasoning_narrative=reasoning_narrative[:500],  # Cap length
        policy_reasoning=policy_reasoning[:200],
        # Evidence
        evidence_contributions=evidence_contributions,
        # Context
        bid_at_assessment=bid,
        ask_at_assessment=ask,
        # Join keys
        entity_id=entity_id,
        correlation_id=correlation_id,
        runtime_session_id=runtime_session_id,
        # Timestamp
        assessed_at_utc=assessed_at_utc,
    )
