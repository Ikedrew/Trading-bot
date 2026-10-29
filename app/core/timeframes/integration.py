"""
Multi-Timeframe Authority — Integration Logic (Placeholder).

Responsibility: Apply HTF constraints to M5 scoring/gating.
Produces: HTFInfluence (score adjustments + blocking decisions)

Ownership: core/timeframes/integration.py
Dependencies: types.py, strategy.signals, core.config
Must NOT import from: cache.py, engine.py

Phase 1: Interface definition only. Returns zero-influence (no-op).
Phase 4: Full constraint application logic.
"""

from __future__ import annotations

from typing import Any

from core.timeframes.types import HTFContext, HTFInfluence
from strategy.signals import Side


def apply_htf_constraints(
    *,
    htf_context: HTFContext,
    signal_side: Side,
    evaluation_bias: Side | None,
    config: Any,
) -> HTFInfluence:
    """
    Apply hierarchical timeframe constraints to M5 scoring.

    Args:
        htf_context: Immutable snapshot of all HTF authority states
        signal_side: Direction of the M5 signal (BUY or SELL)
        evaluation_bias: Current M5 evaluation bias direction
        config: Configuration module (for threshold values)

    Returns:
        HTFInfluence with scoring adjustments and/or blocking decisions.

    Contract:
        - Pure function (no state mutation)
        - No MT5 calls
        - No EngineState writes
        - Only transforms scoring inputs
        - Deterministic: same inputs → same output

    Phase 1: Returns zero-influence (no-op). System behaves as if MTF disabled.
    Phase 4: Full constraint logic (regime penalties, bias gating, structure gates).
    """
    # Phase 1: no-op — returns zero influence
    return HTFInfluence(
        score_adjustment=0.0,
        min_score_adjustment=0.0,
        directional_block=False,
        structural_block=False,
        block_reason="",
        breakdown={},
    )
