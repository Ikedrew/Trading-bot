"""
Candidate Evaluation Bridge — Connects CandidateEvaluator results to the CandidateRegistry lifecycle.

Responsibilities:
    1. Build matched prospective pairs via the shared pairing contract
       (research_engine.lifecycle.candidate_pairing)
    2. Run evaluation for a specific candidate
    3. Persist the evaluation result as a ValidationEntry
    4. Transition the candidate to the appropriate lifecycle state
    5. Return the complete evaluation

Lifecycle mapping:
    VALIDATED   → candidate transitions to VALIDATED (or READY_FOR_REVIEW
                  when coming from SHADOW_TESTING)
    REJECTED    → candidate transitions to REJECTED (or FAILED_VALIDATION)
    INCONCLUSIVE → candidate remains in current state (eligible for more evidence)

This module NEVER modifies production V10 or promotes candidates automatically.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from research_engine.lifecycle.candidate_evaluator import (
    CandidateEvaluation,
    CandidateEvaluator,
    EvaluationConfig,
)

logger = logging.getLogger(__name__)

# Audit-trail destination for full CandidateEvaluation records. Module-level so
# alternate runtimes (tests) can redirect persistence via the same module-attr
# injection pattern used across the persistence layer; default path unchanged.
_EVALUATIONS_DIR = Path("logs/research_lifecycle/evaluations")


def evaluate_candidate(
    candidate_id: str,
    *,
    shadow_observations: list[dict[str, Any]] | None = None,
    candidate_records: list[dict[str, Any]] | None = None,
    incumbent_records: list[dict[str, Any]] | None = None,
    config: EvaluationConfig | None = None,
    registry_dir: str | None = None,
) -> CandidateEvaluation:
    """
    Complete evaluation lifecycle for a candidate.
    
    1. Loads candidate from registry
    2. Confirms candidate is eligible for evaluation
    3. Builds matched prospective pairs via the shared pairing contract
       (candidate shadows ↔ incumbent realised outcomes, exact correlation_id;
       populations are injected or loaded through the sanctioned S3 layer)
    4. Runs CandidateEvaluator on the matched pairs
    5. Persists validation result to candidate's history
    6. Transitions candidate lifecycle state (if decision is terminal)
    7. Returns complete evaluation
    
    GOVERNANCE: VALIDATED does NOT mean PROMOTED. Human approval still required.
    
    Args:
        candidate_id: The candidate to evaluate
        shadow_observations: DEPRECATED alias for ``candidate_records``
            (candidate-shadow CLOSE records, dataset ``shadow_trades`` shape).
        candidate_records: Injected candidate-shadow records (testing);
            loaded via the sanctioned S3 layer when None.
        incumbent_records: Injected incumbent trade_truth records (testing);
            loaded via the sanctioned S3 layer when None.
        config: Evaluation configuration (defaults to standard thresholds)
        registry_dir: Override for CandidateRegistry storage (testing)
    
    Returns:
        CandidateEvaluation with full metrics and decision
    """
    from research_engine.v10.candidates.candidate_registry import CandidateRegistry
    from research_engine.v10.candidates.models import CandidateStatus

    # ─── 1. LOAD CANDIDATE ────────────────────────────────────────────
    registry = CandidateRegistry(storage_dir=registry_dir) if registry_dir else CandidateRegistry()
    candidate = registry.get(candidate_id)

    if candidate is None:
        return _failed_evaluation(candidate_id, "Candidate not found in registry")

    # ─── 2. CONFIRM ELIGIBILITY ───────────────────────────────────────
    eligible_states = {CandidateStatus.VALIDATING, CandidateStatus.SHADOW_TESTING}
    if candidate.status not in eligible_states:
        return _failed_evaluation(candidate_id,
                                   f"Candidate in state '{candidate.status}', "
                                   f"expected one of {eligible_states}")

    # ─── 3+4. MATCHED PAIRS + EVALUATOR (shared pairing contract) ────
    # The evaluator delegates pairing to research_engine.lifecycle.
    # candidate_pairing — the same implementation the auto-evaluator's pair
    # counter uses, so count and evaluation can never drift.
    if shadow_observations is not None and candidate_records is None:
        candidate_records = shadow_observations  # deprecated alias

    evaluator = CandidateEvaluator(config=config or EvaluationConfig())
    evaluation = evaluator.evaluate(
        candidate_id=candidate_id,
        candidate_activated_at=candidate.created_at,
        candidate_records=candidate_records,
        incumbent_records=incumbent_records,
    )

    # ─── 5. PERSIST VALIDATION RESULT ─────────────────────────────────
    # Map CandidateEvaluation fields to ValidationEntry fields
    decision_map = {
        "VALIDATED": "IMPROVED",
        "REJECTED": "WORSENED",
        "INCONCLUSIVE": "INCONCLUSIVE",
    }
    mapped_decision = decision_map.get(evaluation.decision, "INCONCLUSIVE")

    # Encode additional metrics into regressions field as structured info
    regressions = []
    if evaluation.oos_n > 0 and evaluation.oos_delta_r <= 0:
        regressions.append(f"OOS_delta={evaluation.oos_delta_r:+.4f}")
    if not evaluation.survives_outlier_removal:
        regressions.append("fails_outlier_removal")
    if evaluation.symbols_positive < 2:
        regressions.append(f"symbols_positive={evaluation.symbols_positive}")

    try:
        registry.add_validation_result(
            candidate_id=candidate_id,
            validation_id=evaluation.evaluation_id,
            decision=mapped_decision,
            confidence=evaluation.confidence,
            sample_size=evaluation.n,
            expectancy_delta=evaluation.mean_delta_r,
            regressions=regressions,
        )
    except Exception as e:
        logger.warning("[CANDIDATE_EVAL_BRIDGE] Failed to persist validation: %s", str(e)[:100])

    # ─── 6. PERSIST FULL EVALUATION ──────────────────────────────────
    # Save the complete CandidateEvaluation (with CI, p-value, OOS, robustness)
    # alongside the condensed ValidationEntry for full audit trail reconstruction.
    _persist_full_evaluation(candidate_id, evaluation)

    # ─── 7. LIFECYCLE TRANSITION ──────────────────────────────────────
    # Transition target depends on the CURRENT candidate state:
    #   From VALIDATING: VALIDATED (positive) / FAILED_VALIDATION (negative, allows retry)
    #   From SHADOW_TESTING: READY_FOR_REVIEW (positive) / REJECTED (negative)
    if evaluation.decision == "VALIDATED":
        if candidate.status == CandidateStatus.SHADOW_TESTING:
            _safe_transition(registry, candidate_id, CandidateStatus.READY_FOR_REVIEW)
        else:
            _safe_transition(registry, candidate_id, CandidateStatus.VALIDATED)
    elif evaluation.decision == "REJECTED":
        if candidate.status == CandidateStatus.SHADOW_TESTING:
            _safe_transition(registry, candidate_id, CandidateStatus.REJECTED)
        else:
            # Use FAILED_VALIDATION (allows retry) rather than terminal REJECTED
            _safe_transition(registry, candidate_id, CandidateStatus.FAILED_VALIDATION)
    # INCONCLUSIVE: no transition — candidate remains eligible for more evidence

    return evaluation


def _safe_transition(registry, candidate_id: str, target_status: str) -> None:
    """Attempt lifecycle transition. Log but don't raise on failure."""
    try:
        registry.update_status(candidate_id, target_status)
    except ValueError as e:
        logger.warning("[CANDIDATE_EVAL_BRIDGE] Transition failed: %s", str(e)[:100])


def _failed_evaluation(candidate_id: str, reason: str) -> CandidateEvaluation:
    """Create an evaluation representing a failed/blocked evaluation attempt."""
    return CandidateEvaluation(
        candidate_id=candidate_id,
        decision="INCONCLUSIVE",
        decision_reason=f"Evaluation blocked: {reason}",
        confidence="INSUFFICIENT",
    )


def _persist_full_evaluation(candidate_id: str, evaluation: CandidateEvaluation) -> None:
    """
    Persist the complete CandidateEvaluation to disk for audit trail.

    Writes to: logs/research_lifecycle/evaluations/{candidate_id}.jsonl
    Each line is one full evaluation run (appended, never overwritten).
    """
    try:
        eval_dir = _EVALUATIONS_DIR
        eval_dir.mkdir(parents=True, exist_ok=True)
        eval_path = eval_dir / f"{candidate_id}.jsonl"

        import os
        line = json.dumps(evaluation.to_dict(), separators=(",", ":"), default=str) + "\n"
        fd = os.open(str(eval_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception as e:
        logger.debug("[CANDIDATE_EVAL_BRIDGE] Failed to persist full evaluation: %s", str(e)[:100])

# NOTE (Phase 1I-C repair): the former _load_shadow_observations() helper —
# which loaded the canonical nshadow_* shadow_runtime_v1 stream — was removed.
# That stream NEVER contains candidate shadows (runtime-minted nshadow_* IDs
# only), so it could never supply candidate evidence. Pairing populations are
# now owned exclusively by research_engine.lifecycle.candidate_pairing:
# candidate shadows come from the shadow_trades dataset
# (shadow_type=CANDIDATE_<id>) and incumbents from trade_truth, joined by the
# exact execution correlation_id.
