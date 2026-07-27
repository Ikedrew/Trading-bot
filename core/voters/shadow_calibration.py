"""
Shadow Calibration Logger — Structured comparison between production and voter pipeline.

Purely observational. Never affects execution. Never raises to caller.
Produces structured calibration logs for voter system validation.

Ownership: core/voters/shadow_calibration.py
Mutability: NONE
Dependencies: VoteResult, ConfluenceDecision, ExecutionGateResult only
"""

from __future__ import annotations

import logging
from typing import Literal

from core.voters.types import VoteResult
from core.voters.confluence_engine import ConfluenceDecision
from core.voters.execution_gate import ExecutionGateResult

logger = logging.getLogger(__name__)


# ─── DIVERGENCE CLASSIFICATION ────────────────────────────────────────────────

def classify_divergence(
    production_action: str,
    shadow_action: str,
) -> str:
    """
    Classify the relationship between production and shadow decisions.

    Returns one of:
      AGREE_BUY, AGREE_SELL, AGREE_NO_TRADE
      SHADOW_MORE_AGGRESSIVE, SHADOW_MORE_CONSERVATIVE
      DIRECTIONAL_CONFLICT
    """
    if production_action == shadow_action:
        return f"AGREE_{production_action}"

    # Directional conflict: opposite directions
    if (production_action == "BUY" and shadow_action == "SELL") or \
       (production_action == "SELL" and shadow_action == "BUY"):
        return "DIRECTIONAL_CONFLICT"

    # Shadow wants to trade but production doesn't
    if production_action == "NO_TRADE" and shadow_action in ("BUY", "SELL"):
        return "SHADOW_MORE_AGGRESSIVE"

    # Production wants to trade but shadow doesn't
    if shadow_action == "NO_TRADE" and production_action in ("BUY", "SELL"):
        return "SHADOW_MORE_CONSERVATIVE"

    return "UNKNOWN"


# ─── STRUCTURED CALIBRATION LOG ───────────────────────────────────────────────

def emit_shadow_calibration(
    *,
    symbol: str,
    bias_vote: VoteResult,
    structure_vote: VoteResult,
    session_vote: VoteResult,
    confluence: ConfluenceDecision,
    gate: ExecutionGateResult,
    production_action: str,
) -> None:
    """
    Emit structured calibration log comparing production vs shadow pipeline.

    Never raises. Never affects execution. Purely observational.

    Args:
        symbol: Trading symbol
        bias_vote: BiasVoter result
        structure_vote: StructureVoter result
        session_vote: SessionVoter result
        confluence: ConfluenceDecision from voter aggregation
        gate: ExecutionGateResult from safety filter
        production_action: The actual production pipeline decision ("BUY"/"SELL"/"NO_TRADE")
    """
    try:
        shadow_action = confluence.action if gate.allowed else "NO_TRADE"
        classification = classify_divergence(production_action, shadow_action)
        agreement = production_action == shadow_action

        logger.debug(
            "[SHADOW_CALIBRATION] symbol=%s "
            "bias(score=%.3f conf=%.3f reason=%s) "
            "structure(score=%.3f conf=%.3f reason=%s) "
            "session(score=%.3f conf=%.3f reason=%s) "
            "confluence(score=%.3f conf=%.3f action=%s risk=%s conflict=%s) "
            "gate(allowed=%s blockers=%s) "
            "comparison(production=%s shadow=%s agreement=%s class=%s)",
            symbol,
            bias_vote.score, bias_vote.confidence, bias_vote.reason[:40],
            structure_vote.score, structure_vote.confidence, structure_vote.reason[:40],
            session_vote.score, session_vote.confidence, session_vote.reason[:40],
            confluence.score, confluence.confidence, confluence.action,
            confluence.risk_flag, confluence.conflict_flag,
            gate.allowed, gate.blocked_by,
            production_action, shadow_action, agreement, classification,
        )
    except Exception:
        pass  # Calibration logging must never affect runtime
