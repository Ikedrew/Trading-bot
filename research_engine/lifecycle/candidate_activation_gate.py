"""
Candidate Activation Gate — Automatically activates eligible PROPOSED candidates for shadow testing.

A PROPOSED candidate is eligible for direct activation to SHADOW_TESTING when:
    1. Its source hypothesis concluded VALIDATED (it was born from proven evidence)
    2. Its change_definition type is shadow-testable (can be represented through ShadowTradeEngine)
    3. It has not been manually rejected or archived

This gate is called periodically by the research cycle runner. It does NOT:
    - Modify production trading
    - Call MT5Execution or broker
    - Change live configuration
    - Skip human governance for promotion

It ONLY transitions candidates from observation-waiting (PROPOSED) to observation-collecting
(SHADOW_TESTING), allowing the candidate shadow hook to begin paired observations.

Lifecycle flow enabled:
    PROPOSED (born from VALIDATED conclusion)
        ↓ [this gate]
    SHADOW_TESTING
        ↓ [candidate_shadow_hook opens paired observations]
    ... accumulate evidence ...
        ↓ [candidate_auto_evaluator triggers evaluation]
    VALIDATED / FAILED_VALIDATION / INCONCLUSIVE

This module NEVER modifies production V10.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus

logger = logging.getLogger(__name__)

# Candidate types that can be represented through ShadowTradeEngine.open_trade()
_SHADOW_TESTABLE_TYPES: set[str] = {
    "direction_inversion",
    "geometry_modification",
    "regime_conditioning",
    "symbol_exclusion",
}

# Types that cannot produce meaningful shadow observations
_UNSHADOWABLE_TYPES: set[str] = {
    "score_recalibration",
    "pattern_weighting",
    "research_recommendation",
}


@dataclass
class ActivationResult:
    """Result of one activation gate cycle."""
    candidates_scanned: int = 0
    candidates_activated: int = 0
    candidates_skipped: int = 0
    candidates_ineligible: int = 0
    activations: list[dict[str, str]] = field(default_factory=list)
    skips: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates_scanned": self.candidates_scanned,
            "candidates_activated": self.candidates_activated,
            "candidates_skipped": self.candidates_skipped,
            "candidates_ineligible": self.candidates_ineligible,
            "activations": self.activations,
            "skips": self.skips,
        }


def activate_eligible_candidates(
    *,
    registry_dir: str | None = None,
    max_activations: int = 3,
) -> ActivationResult:
    """
    Scan PROPOSED candidates and activate those eligible for shadow testing.

    Eligibility criteria:
        1. Status is PROPOSED
        2. change_definition.type is in _SHADOW_TESTABLE_TYPES
        3. Candidate was created from a research lifecycle hypothesis
           (has hypothesis_id — meaning it came from a VALIDATED conclusion)

    Args:
        registry_dir: Override storage dir for testing
        max_activations: Maximum candidates to activate per cycle (prevents flood)

    Returns:
        ActivationResult with details of what happened

    Never raises — all errors logged and suppressed.
    """
    from research_engine.v10.candidates.candidate_registry import CandidateRegistry

    result = ActivationResult()

    try:
        registry = CandidateRegistry(storage_dir=registry_dir) if registry_dir else CandidateRegistry()
        proposed = registry.list_by_status(CandidateStatus.PROPOSED)
        result.candidates_scanned = len(proposed)

        if not proposed:
            return result

        activated = 0

        for candidate in proposed:
            if activated >= max_activations:
                break

            eligible, reason = _check_eligibility(candidate)

            if not eligible:
                result.candidates_ineligible += 1
                result.skips.append({
                    "candidate_id": candidate.candidate_id,
                    "reason": reason,
                })
                logger.debug(
                    "[ACTIVATION_GATE] Skipped %s: %s",
                    candidate.candidate_id, reason,
                )
                continue

            # Activate: PROPOSED → SHADOW_TESTING
            try:
                registry.update_status(candidate.candidate_id, CandidateStatus.SHADOW_TESTING)
                activated += 1
                result.candidates_activated += 1
                result.activations.append({
                    "candidate_id": candidate.candidate_id,
                    "change_type": candidate.change_definition.get("type", ""),
                    "hypothesis_id": candidate.hypothesis_id,
                })
                logger.info(
                    "[ACTIVATION_GATE] Activated %s → SHADOW_TESTING (type=%s)",
                    candidate.candidate_id,
                    candidate.change_definition.get("type", ""),
                )
            except (ValueError, Exception) as e:
                result.candidates_skipped += 1
                result.skips.append({
                    "candidate_id": candidate.candidate_id,
                    "reason": f"Transition failed: {str(e)[:80]}",
                })
                logger.warning(
                    "[ACTIVATION_GATE] Transition failed for %s: %s",
                    candidate.candidate_id, str(e)[:100],
                )

    except Exception as e:
        logger.warning("[ACTIVATION_GATE] Gate execution failed: %s", str(e)[:150])

    return result


def _check_eligibility(candidate: CandidateRecord) -> tuple[bool, str]:
    """
    Determine whether a PROPOSED candidate is eligible for automatic activation.

    Returns (eligible, reason).
    """
    # Must have a hypothesis_id (created from research lifecycle)
    if not candidate.hypothesis_id:
        return False, "No hypothesis_id — not from research lifecycle"

    # Must have a change_definition with a type
    change_type = candidate.change_definition.get("type", "")
    if not change_type:
        return False, "No change_definition type"

    # Must be shadow-testable
    if change_type in _UNSHADOWABLE_TYPES:
        return False, f"Type '{change_type}' cannot be shadow-tested"

    if change_type not in _SHADOW_TESTABLE_TYPES:
        return False, f"Type '{change_type}' is unknown/unsupported for shadow testing"

    # Must have baseline_id (confirms it references a real baseline)
    if not candidate.baseline_id:
        return False, "No baseline_id — cannot compare"

    return True, "Eligible"
