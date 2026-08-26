"""
Candidate Auto-Evaluator — Automatically evaluates SHADOW_TESTING candidates when minimum N is reached.

Called periodically by the research cycle runner. For each candidate in SHADOW_TESTING:
    1. Counts available paired observations (prospective only)
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

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SHADOW_DIR = Path("logs/shadow_trades")

# Minimum number of paired observations before triggering evaluation
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
    shadow_dir: str | None = None,
    minimum_pairs: int = _DEFAULT_MINIMUM_PAIRS,
    max_evaluations: int = 2,
) -> AutoEvaluationResult:
    """
    Scan SHADOW_TESTING candidates and evaluate those with sufficient paired evidence.

    For each candidate in SHADOW_TESTING:
        1. Count prospective paired observations vs a baseline population
           (RETIRED in Phase 1I-C: the old V10_PRIMARY baseline no longer
           exists; pair counting returns 0 until a canonical-lineage baseline
           is defined, so candidates remain in SHADOW_TESTING)
        2. If pairs >= minimum_pairs, call evaluate_candidate()
        3. Let the bridge handle lifecycle transitions

    Args:
        registry_dir: Override storage dir for testing
        shadow_dir: Override shadow observations dir for testing
        minimum_pairs: Minimum paired observations before evaluation (default 30)
        max_evaluations: Maximum evaluations per cycle (prevents overload)

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

        # Load all shadow observations once (shared across candidates)
        observations = _load_observations(shadow_dir)
        if not observations:
            result.candidates_insufficient = len(shadow_testing)
            return result

        evaluated = 0

        for candidate in shadow_testing:
            if evaluated >= max_evaluations:
                result.candidates_skipped += len(shadow_testing) - result.candidates_evaluated - result.candidates_insufficient
                break

            try:
                # Count prospective pairs for this candidate
                pair_count = _count_prospective_pairs(
                    candidate_id=candidate.candidate_id,
                    candidate_created_at=candidate.created_at,
                    observations=observations,
                )

                if pair_count < minimum_pairs:
                    result.candidates_insufficient += 1
                    logger.debug(
                        "[AUTO_EVAL] %s: insufficient pairs (%d/%d)",
                        candidate.candidate_id, pair_count, minimum_pairs,
                    )
                    continue

                # Trigger evaluation via the existing bridge
                from research_engine.lifecycle.candidate_evaluation_bridge import evaluate_candidate

                evaluation = evaluate_candidate(
                    candidate.candidate_id,
                    shadow_observations=observations,
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


def _count_prospective_pairs(
    *,
    candidate_id: str,
    candidate_created_at: str,
    observations: list[dict[str, Any]],
) -> int:
    """
    Count the number of paired (baseline + candidate) observations available
    for this candidate, considering only prospective data (after created_at).

    RETIRED (Phase 1I-C): the legacy baseline population was
    shadow_type == "V10_PRIMARY", which has been removed from the architecture.
    The canonical Horizon Shadow lineage is NOT a semantically equivalent
    baseline (different geometry source, different identity model), so no
    artificial substitution is made. Pair counting therefore always returns 0
    and candidates simply remain in SHADOW_TESTING pending insufficient
    evidence. A later phase may define an honest candidate-vs-canonical-lineage
    comparison.

    Returns 0 unconditionally.
    """
    return 0


def _load_observations(shadow_dir: str | None = None) -> list[dict[str, Any]]:
    """Load all shadow trade observations from disk."""
    obs_dir = Path(shadow_dir) if shadow_dir else _SHADOW_DIR
    observations = []
    if not obs_dir.exists():
        return []
    for f in obs_dir.rglob("*.jsonl"):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    observations.append(json.loads(line))
        except Exception:
            continue
    return observations


def _parse_timestamp(ts_str: str) -> float:
    """Parse ISO timestamp to unix epoch. Returns 0.0 on failure."""
    if not ts_str:
        return 0.0
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


def _get_entry_time(obs: dict) -> float | None:
    """Extract entry timestamp from observation (v2 nested or flat)."""
    # v2 schema: timestamps.entry_timestamp
    ts = obs.get("timestamps", {}).get("entry_timestamp")
    if ts:
        return float(ts)
    # Flat schema: entry_time
    et = obs.get("entry_time")
    if et:
        return float(et)
    return None


def _get_entity_id(obs: dict) -> str:
    """Extract entity_id from observation (v2 nested or flat)."""
    # v2 schema: identity.entity_id
    eid = obs.get("identity", {}).get("entity_id")
    if eid:
        return eid
    # Flat schema
    return obs.get("entity_id", "")


def _get_shadow_type(obs: dict) -> str:
    """Extract shadow_type from observation (v2 nested or flat)."""
    # v2 schema: identity.shadow_type
    st = obs.get("identity", {}).get("shadow_type")
    if st:
        return st
    # Flat schema
    return obs.get("shadow_type", "")
