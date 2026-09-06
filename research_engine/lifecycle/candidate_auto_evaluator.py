"""
Candidate Auto-Evaluator — Automatically evaluates SHADOW_TESTING candidates when minimum N is reached.

Called periodically by the research cycle runner. For each candidate in SHADOW_TESTING:
    1. Counts available MATCHED PROSPECTIVE PAIRS (candidate shadow ↔ incumbent
       realised outcome on the same opportunity — see
       research_engine.lifecycle.candidate_pairing for the pairing contract)
    2. If paired count >= minimum_sample (default 30), triggers evaluation
    3. Evaluation transitions the candidate via the existing bridge

This module NEVER:
    - Modifies production trading
    - Calls MT5Execution or broker
    - Changes live configuration
    - Promotes candidates to production
    - Bypasses human governance

Lifecycle interaction:
    SHADOW_TESTING + enough evidence → evaluate_candidate()
        → VALIDATED (prospective evidence confirms improvement)
        → FAILED_VALIDATION (prospective evidence shows harm — allows retry)
        → INCONCLUSIVE (stays in SHADOW_TESTING, waits for more evidence)

IMPORTANT: A candidate reaching VALIDATED here means its PROSPECTIVE shadow evidence
confirms the proposed change works. It does NOT mean it's approved for production.
Human governance is still required for promotion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from research_engine.lifecycle.candidate_pairing import (
    count_prospective_pairs,
)

logger = logging.getLogger(__name__)

# Minimum number of MATCHED prospective pairs before triggering evaluation
_DEFAULT_MINIMUM_PAIRS = 30


@dataclass
class AutoEvaluationResult:
    """Result of one auto-evaluation cycle."""
    candidates_scanned: int = 0
    candidates_evaluated: int = 0
    candidates_insufficient: int = 0
    candidates_skipped: int = 0
    evaluations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates_scanned": self.candidates_scanned,
            "candidates_evaluated": self.candidates_evaluated,
            "candidates_insufficient": self.candidates_insufficient,
            "candidates_skipped": self.candidates_skipped,
            "evaluations": self.evaluations,
        }


def auto_evaluate_candidates(
    *,
    registry_dir: str | None = None,
    minimum_pairs: int = _DEFAULT_MINIMUM_PAIRS,
    max_evaluations: int = 2,
    candidate_records: list[dict[str, Any]] | None = None,
    incumbent_records: list[dict[str, Any]] | None = None,
) -> AutoEvaluationResult:
    """
    Scan SHADOW_TESTING candidates and evaluate those with sufficient matched evidence.

    For each candidate in SHADOW_TESTING:
        1. Count MATCHED PROSPECTIVE PAIRS via the shared pairing contract
           (candidate shadow ↔ incumbent realised outcome on the same
           opportunity; see research_engine.lifecycle.candidate_pairing)
        2. If pairs >= minimum_pairs, call evaluate_candidate()
        3. Let the bridge handle lifecycle transitions

    Args:
        registry_dir: Override storage dir for testing
        minimum_pairs: Minimum matched pairs before evaluation (default 30)
        max_evaluations: Maximum evaluations per cycle (prevents overload)
        candidate_records: Injected candidate-shadow CLOSE records (testing);
            loaded via the sanctioned S3 layer when None.
        incumbent_records: Injected incumbent trade_truth records (testing);
            loaded via the sanctioned S3 layer when None.

    Returns:
        AutoEvaluationResult with details of what happened

    Never raises — all errors logged and suppressed.
    """
    from research_engine.v10.candidates.candidate_registry import CandidateRegistry
    from research_engine.v10.candidates.models import CandidateStatus

    result = AutoEvaluationResult()

    try:
        registry = CandidateRegistry(storage_dir=registry_dir) if registry_dir else CandidateRegistry()
        shadow_testing = registry.list_by_status(CandidateStatus.SHADOW_TESTING)
        result.candidates_scanned = len(shadow_testing)

        if not shadow_testing:
            return result

        evaluated = 0

        for candidate in shadow_testing:
            if evaluated >= max_evaluations:
                result.candidates_skipped += len(shadow_testing) - result.candidates_evaluated - result.candidates_insufficient
                break

            try:
                # Count MATCHED prospective pairs for this candidate via the
                # shared pairing contract (same implementation the evaluator
                # consumes — count and evaluation cannot drift).
                pair_count = count_prospective_pairs(
                    candidate_id=candidate.candidate_id,
                    candidate_activated_at=candidate.created_at,
                    candidate_records=candidate_records,
                    incumbent_records=incumbent_records,
                )

                if pair_count < minimum_pairs:
                    result.candidates_insufficient += 1
                    logger.debug(
                        "[AUTO_EVAL] %s: insufficient pairs (%d/%d)",
                        candidate.candidate_id, pair_count, minimum_pairs,
                    )
                    continue

                # Trigger evaluation via the existing bridge — pass the SAME
                # injected populations so the evaluator's pair population is
                # identical to the population just counted.
                from research_engine.lifecycle.candidate_evaluation_bridge import evaluate_candidate

                evaluation = evaluate_candidate(
                    candidate.candidate_id,
                    candidate_records=candidate_records,
                    incumbent_records=incumbent_records,
                    registry_dir=registry_dir,
                )

                evaluated += 1
                result.candidates_evaluated += 1
                result.evaluations.append({
                    "candidate_id": candidate.candidate_id,
                    "pairs_available": pair_count,
                    "decision": evaluation.decision,
                    "confidence": evaluation.confidence,
                    "mean_delta_r": evaluation.mean_delta_r,
                    "n": evaluation.n,
                })

                logger.info(
                    "[AUTO_EVAL] Evaluated %s: decision=%s n=%d delta_r=%+.4f",
                    candidate.candidate_id,
                    evaluation.decision,
                    evaluation.n,
                    evaluation.mean_delta_r,
                )

            except Exception as e:
                result.candidates_skipped += 1
                logger.warning(
                    "[AUTO_EVAL] Failed for %s: %s",
                    candidate.candidate_id, str(e)[:100],
                )

    except Exception as e:
        logger.warning("[AUTO_EVAL] Auto-evaluation cycle failed: %s", str(e)[:150])

    return result

# NOTE (Phase 1I-C repair): the former _count_prospective_pairs() (unconditional
# `return 0`), _load_observations() (nshadow_* stream — cannot contain candidate
# shadows) and the flat/nested field-extraction helpers were removed. Pair
# counting and pair extraction now live exclusively in
# research_engine.lifecycle.candidate_pairing, which both this evaluator and
# the CandidateEvaluator consume — one pairing contract, no drift.
