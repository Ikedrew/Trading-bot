"""
Execution Policy Engine — EV-first trade permission system.

Determines whether a trade should be executed based on:
    1. Expected Value (PRIMARY — must be positive)
    2. Market State (execution environment gate)
    3. RR threshold (SECONDARY — structural feasibility check)
    4. Score/confidence floors (noise rejection)

OLD SYSTEM: RR → determines trade acceptance
NEW SYSTEM: EV + uncertainty → determines trade acceptance
            RR → only validates structural feasibility

This layer does NOT:
    - Compute scores
    - Classify strategies
    - Detect patterns
    - Evaluate market structure

It only CONSUMES upstream outputs and produces execution parameters.

Design: deterministic, no learning, no adaptation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.pipeline.market_state_engine import MarketState, MarketStateResult
from core.pipeline.expected_value import ExpectedValueResult


# ─── POLICY OUTPUT ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExecutionPolicy:
    """Immutable execution parameters for this cycle."""
    trade_allowed: bool
    block_reason: str | None        # None if trade_allowed=True
    required_rr: float              # Minimum R:R ratio required
    max_position_fraction: float    # 0.0–1.0 of full position allowed
    market_state: MarketState
    ev_positive: bool               # Expected value check result
    ev_value: float                 # Raw EV number
    policy_reasoning: str


# ─── POLICY THRESHOLDS ────────────────────────────────────────────────────────

# RR requirements per market state (secondary validation only)
_RR_STRUCTURED = 1.5
_RR_TRANSITIONAL_MIN = 1.8
_RR_TRANSITIONAL_MAX = 2.5
_RR_CHOP = 999.0

# Sizing per market state
_SIZE_STRUCTURED = 1.0
_SIZE_TRANSITIONAL = 0.5
_SIZE_CHOP = 0.0

# Score thresholds for trade permission
_MIN_NEUTRAL_SCORE = 0.20
_MIN_STRATEGY_CONFIDENCE = 0.15


# ─── POLICY ENGINE ────────────────────────────────────────────────────────────

def compute_execution_policy(
    *,
    market_state_result: MarketStateResult,
    assessment: Any,
    ev_result: ExpectedValueResult | None = None,
) -> ExecutionPolicy:
    """
    Compute execution parameters using EV-first logic.

    Receives an OpportunityAssessment as the single analytical input.

    Gate priority:
        1. Market state CHOP → block
        2. Neutral score floor → block
        3. Strategy confidence floor → block
        4. EV must be positive → block if negative
        5. RR must meet threshold → block if insufficient

    Args:
        market_state_result: Output from MarketStateEngine
        assessment: OpportunityAssessment (frozen analytical snapshot)
        ev_result: Optional ExpectedValueResult (None = pre-risk stage, skip EV check)

    Returns:
        ExecutionPolicy with EV-informed permission decision
    """
    # Extract analytical fields from assessment
    score_neutral = assessment.score_neutral if assessment else 0.0
    score_strategy = assessment.score_strategy if assessment else 0.0
    strategy_confidence = assessment.strategy_confidence if assessment else 0.0

    state = market_state_result.state

    # ─── GATE 1: CHOP — NO LONGER A HARD BLOCK ───────────────────────
    # CHOP reduces probability through uncertainty dampening in EV layer.
    # It does NOT block the pipeline outright.
    # (Removed: hard return on CHOP state)

    # ─── GATE 2: NEUTRAL SCORE FLOOR ─────────────────────────────────
    if score_neutral < _MIN_NEUTRAL_SCORE:
        return ExecutionPolicy(
            trade_allowed=False,
            block_reason="NEUTRAL_SCORE_BELOW_MINIMUM",
            required_rr=_RR_TRANSITIONAL_MAX,
            max_position_fraction=0.0,
            market_state=state,
            ev_positive=False,
            ev_value=0.0,
            policy_reasoning=f"Neutral score {score_neutral:.3f} below minimum {_MIN_NEUTRAL_SCORE}",
        )

    # ─── GATE 3: STRATEGY CONFIDENCE FLOOR ────────────────────────────
    # Only applies when a strategy WAS selected but with insufficient confidence.
    # When strategy_confidence == 0.0, it means no strategy was selected (advisory fallback)
    # which is a valid execution path using global weights — NOT a block condition.
    if 0.0 < strategy_confidence < _MIN_STRATEGY_CONFIDENCE:
        return ExecutionPolicy(
            trade_allowed=False,
            block_reason="STRATEGY_CONFIDENCE_TOO_LOW",
            required_rr=_RR_TRANSITIONAL_MAX,
            max_position_fraction=0.0,
            market_state=state,
            ev_positive=False,
            ev_value=0.0,
            policy_reasoning=f"Strategy confidence {strategy_confidence:.3f} below minimum {_MIN_STRATEGY_CONFIDENCE}",
        )

    # ─── DETERMINE RR THRESHOLD ───────────────────────────────────────
    if state == MarketState.CHOP:
        # CHOP: stricter RR requirement + reduced size (but NOT blocked)
        required_rr = _RR_TRANSITIONAL_MAX  # Highest RR bar
        size_fraction = _SIZE_TRANSITIONAL * 0.5  # Quarter size in CHOP
    elif state == MarketState.TRANSITIONAL:
        delta_stab = market_state_result.delta_stability
        required_rr = _RR_TRANSITIONAL_MIN + (_RR_TRANSITIONAL_MAX - _RR_TRANSITIONAL_MIN) * (1.0 - delta_stab)
        required_rr = round(required_rr, 2)
        size_fraction = _SIZE_TRANSITIONAL
    else:
        required_rr = _RR_STRUCTURED
        size_fraction = _SIZE_STRUCTURED

    # ─── GATE 4: EXPECTED VALUE (PRIMARY DECISION) ────────────────────
    if ev_result is not None:
        if not ev_result.ev_positive:
            return ExecutionPolicy(
                trade_allowed=False,
                block_reason="NEGATIVE_EXPECTED_VALUE",
                required_rr=required_rr,
                max_position_fraction=0.0,
                market_state=state,
                ev_positive=False,
                ev_value=ev_result.ev,
                policy_reasoning=f"EV={ev_result.ev:.6f} (does not rank above alternatives under current uncertainty)",
            )

        # ─── GATE 5: RR STRUCTURAL FEASIBILITY (SECONDARY) ───────────
        if ev_result.rr_effective < required_rr:
            return ExecutionPolicy(
                trade_allowed=False,
                block_reason="RR_BELOW_THRESHOLD",
                required_rr=required_rr,
                max_position_fraction=0.0,
                market_state=state,
                ev_positive=True,
                ev_value=ev_result.ev,
                policy_reasoning=f"EV positive but RR={ev_result.rr_effective:.2f} < required {required_rr:.2f} ({state.value})",
            )

        # ─── ALL GATES PASSED ─────────────────────────────────────────
        return ExecutionPolicy(
            trade_allowed=True,
            block_reason=None,
            required_rr=required_rr,
            max_position_fraction=size_fraction,
            market_state=state,
            ev_positive=True,
            ev_value=ev_result.ev,
            policy_reasoning=f"Selected: ranks above alternatives (EV={ev_result.ev:.6f}) | RR={ev_result.rr_effective:.2f} >= {required_rr:.2f} | {state.value} | size={size_fraction:.0%}",
        )

    # ─── NO EV AVAILABLE (pre-risk stage — allow through for scoring) ─
    return ExecutionPolicy(
        trade_allowed=True,
        block_reason=None,
        required_rr=required_rr,
        max_position_fraction=size_fraction,
        market_state=state,
        ev_positive=False,
        ev_value=0.0,
        policy_reasoning=f"EV not yet computed (pre-risk) | {state.value} | RR_req={required_rr:.2f}",
    )
