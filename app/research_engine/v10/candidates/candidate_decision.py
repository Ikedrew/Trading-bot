"""
Candidate Human Decision — Explicit governed ACCEPT / REJECT for Track-A.

Closes exactly ONE boundary:
    READY_FOR_REVIEW -> explicit human invocation -> durable decision
    -> CandidateRegistry.update_status() -> ACCEPTED or REJECTED

Wave 4E.2 — recommendation-bound human governance (FAIL CLOSED):
    Every human decision now binds to ONE specific canonical
    CandidateRecommendation (Wave 4E.1). Before anything is written, the
    referenced recommendation must exist and its candidate_id / evaluation_id
    / baseline_id / baseline_config_hash provenance must match the candidate's
    own evidence. An ACCEPT additionally requires the recommendation to be
    actionable (VALIDATED, not promotion_blocked, complete provenance,
    sufficient sample, sufficient confidence) and, for REJECT, that the
    recommendation is part of the candidate's recorded evidence. Failures are
    auditable non-effective RECOMMENDATION_BLOCKED rows — never a fabricated
    REJECT, never a substitute recommendation.

Wave 4C.3 — baseline-safe promotion (FAIL CLOSED):
    Before a human ACCEPT crosses the promotion boundary, the candidate's
    recorded baseline is re-proven against the 4C.1 canonical baseline
    authority (validate_candidate_baseline): real baseline_id, == active
    canonical baseline, loadable snapshot, provenance == snapshot config_hash
    == current production config hash. On failure the approval is NOT applied
    (candidate stays READY_FOR_REVIEW), an auditable non-effective
    BASELINE_BLOCKED row preserves the human intent, and no rebase/mutation
    occurs. REJECT is not gated (rejecting a stale candidate stays possible).

CandidateRegistry remains the SOLE lifecycle authority. This module owns only
the human-decision RECORD. NEVER called automatically by ResearchCycleRunner
or the candidate auto-evaluator. NEVER evaluates, shadows, applies, deploys,
or touches baselines.

FAILURE SEMANTICS (file persistence — no DB atomicity):
    1. Preconditions validated BEFORE any write (fail closed, write nothing).
    2. Durable decision row appended FIRST (O_APPEND, never truncates).
    3. Registry.update_status() attempted SECOND.
    4. If the transition fails, the row is marked STATUS_FAILED and
       RuntimeError is raised — detectable, never masquerades as success.
    5. STATUS_FAILED rows are ignored by get_decision(); retry may proceed.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now
from research_engine.v10.candidates.models import CandidateStatus

logger = logging.getLogger(__name__)

_DECISIONS_DIR = "data/research/candidates"
_DECISIONS_FILE = "decisions.jsonl"

_VALID_DECISIONS = ("ACCEPT", "REJECT")

_DECISION_TO_STATUS = {
    "ACCEPT": CandidateStatus.ACCEPTED,
    "REJECT": CandidateStatus.REJECTED,
}


@dataclass
class HumanDecision:
    """One durable explicit human decision for a candidate."""

    candidate_id: str = ""
    decision: str = ""  # ACCEPT | REJECT
    actor: str = ""
    reason: str = ""
    timestamp: str = ""
    evaluation_id: str = ""
    status_before: str = ""
    status_after: str = ""
    outcome: str = "COMPLETED"  # COMPLETED | STATUS_FAILED | RECOMMENDATION_BLOCKED
    error: str = ""
    # ─── Wave 4E.2: recommendation provenance ─────────────────────────
    # Binds this human decision to one specific canonical CandidateRecommendation.
    recommendation_id: str = ""
    treatment_id: str = ""
    treatment_spec: str | None = None
    baseline_id: str = ""
    baseline_config_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "decision": self.decision,
            "actor": self.actor,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "evaluation_id": self.evaluation_id,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "outcome": self.outcome,
            "error": self.error,
            "recommendation_id": self.recommendation_id,
            "treatment_id": self.treatment_id,
            "treatment_spec": self.treatment_spec,
            "baseline_id": self.baseline_id,
            "baseline_config_hash": self.baseline_config_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HumanDecision":
        return cls(
            candidate_id=data.get("candidate_id", ""),
            decision=data.get("decision", ""),
            actor=data.get("actor", ""),
            reason=data.get("reason", ""),
            timestamp=data.get("timestamp", ""),
            evaluation_id=data.get("evaluation_id", ""),
            status_before=data.get("status_before", ""),
            status_after=data.get("status_after", ""),
            outcome=data.get("outcome", "COMPLETED"),
            error=data.get("error", ""),
            recommendation_id=data.get("recommendation_id", ""),
            treatment_id=data.get("treatment_id", ""),
            treatment_spec=data.get("treatment_spec"),
            baseline_id=data.get("baseline_id", ""),
            baseline_config_hash=data.get("baseline_config_hash", ""),
        )


@dataclass
class DecisionResult:
    """Outcome of a record_human_decision() call."""

    ok: bool = False
    duplicate: bool = False
    decision: HumanDecision = field(default_factory=HumanDecision)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "duplicate": self.duplicate,
            "error": self.error,
            "decision": self.decision.to_dict(),
        }


class CandidateDecisionStore:
    """Append-only durable store for Track-A human candidate decisions.

    Persistence: <decisions_dir>/decisions.jsonl (one JSON object per line).
    Reinstantiable over the same directory: restart durability.
    """

    def __init__(self, decisions_dir: str | None = None):
        self._dir = Path(decisions_dir or _DECISIONS_DIR)
        self._decisions: list[HumanDecision] = []
        self._load()

    def get_decision(self, candidate_id: str) -> HumanDecision | None:
        """Effective COMPLETED decision for a candidate, if any."""
        for d in self._decisions:
            if d.candidate_id == candidate_id and d.outcome == "COMPLETED":
                return d
        return None

    def get_decision_by_id(self, recommendation_id: str) -> HumanDecision | None:
        """COMPLETED decision matching a specific recommendation_id."""
        for d in self._decisions:
            if (
                d.recommendation_id == recommendation_id
                and d.outcome == "COMPLETED"
            ):
                return d
        return None

    def list_decisions(self) -> list[HumanDecision]:
        """All COMPLETED decisions."""
        return [d for d in self._decisions if d.outcome == "COMPLETED"]

    def list_all_rows(self) -> list[HumanDecision]:
        """Every persisted row, including STATUS_FAILED markers."""
        return list(self._decisions)

    def _append_row_atomic(self, decision: HumanDecision) -> None:
        """Append one row with O_APPEND (never truncates a concurrent writer)."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / _DECISIONS_FILE
        line = json.dumps(decision.to_dict(), default=str) + "\n"
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)

    def _rewrite_outcome(
        self, candidate_id: str, evaluation_id: str, *, outcome: str, error: str
    ) -> None:
        for d in self._decisions:
            if (
                d.candidate_id == candidate_id
                and d.evaluation_id == evaluation_id
                and d.outcome == "COMPLETED"
            ):
                d.outcome = outcome
                d.error = error
                break
        self._persist()

    def _persist(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / _DECISIONS_FILE
        tmp = path.with_suffix(".tmp")
        lines = [json.dumps(d.to_dict(), default=str) for d in self._decisions]
        tmp.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
        tmp.replace(path)

    def _load(self) -> None:
        path = self._dir / _DECISIONS_FILE
        if not path.exists():
            return
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        self._decisions.append(
                            HumanDecision.from_dict(json.loads(line))
                        )
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        continue
        except OSError:
            return


def _supporting_evaluation_id(candidate) -> str:
    """Latest successful evaluation provenance for an ACCEPT decision.

    The bridge persists evaluation.evaluation_id as ValidationEntry.validation_id
    with mapped decision IMPROVED (see candidate_evaluation_bridge). Returns ""
    when the candidate carries no successful (IMPROVED) validation evidence.
    """
    latest_success = ""
    for entry in getattr(candidate, "validation_history", []) or []:
        if getattr(entry, "decision", "") == "IMPROVED":
            vid = getattr(entry, "validation_id", "") or ""
            if vid:
                latest_success = vid
    return latest_success


def _candidate_evaluation_ids(candidate) -> set[str]:
    """Every evaluation identity the candidate actually carries as evidence.

    The 4D/4C bridge persists ``evaluation.evaluation_id`` as
    ``ValidationEntry.validation_id`` for EVERY evaluation decision (IMPROVED /
    WORSENED / INCONCLUSIVE), so this set is the candidate's durable record of
    which evaluations belong to it. Used to bind a recommendation to the
    candidate's real evidence instead of inferring it from mutable state.
    """
    ids: set[str] = set()
    for entry in getattr(candidate, "validation_history", []) or []:
        vid = getattr(entry, "validation_id", "") or ""
        if vid:
            ids.add(vid)
    return ids


def record_human_decision(
    candidate_id: str,
    decision: str,
    recommendation_id: str,
    *,
    actor: str = "",
    reason: str = "",
    registry_dir: str | None = None,
    decisions_dir: str | None = None,
    recommendations_dir: str | None = None,
    evaluations_dir: str | None = None,
) -> DecisionResult:
    """Record an explicit human ACCEPT/REJECT bound to ONE canonical recommendation.

    Wave 4E.2: every human decision must bind to a specific CandidateRecommendation
    (from 4E.1). The recommendation's provenance must match the candidate's.

    Preconditions (fail closed — nothing written, nothing changed):
        - recommendation_id non-empty and resolves to an existing recommendation
        - recommendation.candidate_id must match candidate_id (no substitution)
        - recommendation.evaluation_id must match the candidate's IMPROVED
          validation evidence
        - ACCEPT requires the recommendation to be actionable=True AND the
          existing Wave 4C.3 baseline safety invariant to pass
        - REJECT requires no positive evidence beyond recommendation binding
        - decision in {ACCEPT, REJECT}
        - actor and reason non-empty
        - candidate exists and status == READY_FOR_REVIEW
        - no conflicting COMPLETED decision already recorded.

    Idempotency: identical (candidate, recommendation, decision, actor, reason)
    replay returns ok=True, duplicate=True, writes no new row. Conflicting
    decisions raise ValueError and preserve the original. No reversal.
    """
    from research_engine.v10.candidates.candidate_registry import CandidateRegistry

    verdict = (decision or "").strip().upper()
    if verdict not in _VALID_DECISIONS:
        raise ValueError(
            f"Invalid decision '{decision}': must be one of {_VALID_DECISIONS}"
        )
    if not (actor or "").strip():
        raise ValueError("Human decision requires a non-empty actor")
    if not (reason or "").strip():
        raise ValueError("Human decision requires a non-empty reason")
    # Wave 4E.2: a human decision must name the ONE canonical recommendation
    # being reviewed. Without a recommendation identity there is no
    # attributable evidence chain, so nothing is written and nothing changes.
    if not (recommendation_id or "").strip():
        raise ValueError(
            f"Human decision for '{candidate_id}' requires a "
            "recommendation_id: cannot decide without a canonical "
            "recommendation to bind to"
        )

    registry = (
        CandidateRegistry(storage_dir=registry_dir)
        if registry_dir
        else CandidateRegistry()
    )
    candidate = registry.get(candidate_id)
    if candidate is None:
        raise ValueError(f"Candidate '{candidate_id}' not found in registry")

    # Ordering with the idempotency check below: an identical replay arrives
    # with the candidate already ACCEPTED/REJECTED, so the duplicate/conflict
    # path must run BEFORE the READY_FOR_REVIEW precondition. All other
    # states without a prior COMPLETED decision still fail closed here.
    store = CandidateDecisionStore(decisions_dir=decisions_dir)
    existing = store.get_decision(candidate_id)

    evaluation_id = _supporting_evaluation_id(candidate)
    known_eval_ids = _candidate_evaluation_ids(candidate)

    def _block_attempt(
        reason_text: str,
        *,
        rec: object | None = None,
    ) -> None:
        """Persist a NON-EFFECTIVE blocked-attempt row, then fail closed.

        Mirrors the existing BASELINE_BLOCKED semantics: human intent
        (decision/actor/reason) is preserved as audit evidence while system
        eligibility is denied. outcome is RECOMMENDATION_BLOCKED, never
        COMPLETED, so get_decision() ignores it and the effective decision
        stays empty. NEVER fabricates the opposite verdict.
        """
        blocked = HumanDecision(
            candidate_id=candidate_id,
            decision=verdict,
            actor=actor.strip(),
            reason=reason.strip(),
            timestamp=timestamp_now(),
            evaluation_id=evaluation_id,
            status_before=candidate.status,
            status_after="",
            outcome="RECOMMENDATION_BLOCKED",
            error=reason_text,
            recommendation_id=(
                rec.recommendation_id
                if rec is not None
                else (recommendation_id or "").strip()
            ),
            treatment_id=getattr(rec, "treatment_id", ""),
            baseline_id=getattr(rec, "baseline_id", ""),
            baseline_config_hash=getattr(rec, "baseline_config_hash", ""),
        )
        store._append_row_atomic(blocked)
        raise ValueError(reason_text)

    # ─── Wave 4E.2: canonical recommendation lookup ─────────────────────────
    # Every human decision must bind to ONE specific canonical
    # CandidateRecommendation: the recommendation is the durable interpretation
    # of the evaluation evidence, and the human decision binds to that
    # interpretation rather than to mutable candidate state.
    #
    # Ordering: the recommendation is RESOLVED here (so idempotency can compare
    # canonical identity) but its provenance is VALIDATED further below, after
    # the pre-existing state / evidence / baseline gates. Every existing gate
    # therefore keeps its original audited ordering and error semantics.
    from research_engine.lifecycle.candidate_recommendation import (
        RecommendationStore,
    )
    rec_store = RecommendationStore(recommendations_dir=recommendations_dir)
    recommendation = rec_store.get_by_recommendation_id(
        recommendation_id.strip()
    )
    if recommendation is None:
        _block_attempt(
            f"recommendation_id '{recommendation_id.strip()}' not found in "
            "the canonical recommendation store: cannot bind a human "
            "decision without recommendation provenance",
            rec=None,
        )

    from research_engine.lifecycle.treatment_provenance import validate_evaluation_spec
    validate_evaluation_spec(recommendation, evaluations_dir)
    if existing is not None and existing.treatment_spec != recommendation.treatment_spec:
        raise ValueError("Decision treatment_spec substitution")

    if existing is not None:
        if (
            existing.decision == verdict
            and existing.recommendation_id == recommendation.recommendation_id
            and existing.evaluation_id == evaluation_id
            and existing.actor == actor.strip()
            and existing.reason == reason.strip()
        ):
            live_registry = (
                CandidateRegistry(storage_dir=registry_dir)
                if registry_dir
                else CandidateRegistry()
            )
            live = live_registry.get(candidate_id)
            if live is not None and live.status == existing.status_after:
                return DecisionResult(ok=True, duplicate=True, decision=existing)
            raise RuntimeError(
                f"Candidate '{candidate_id}' has COMPLETED decision "
                f"{existing.decision} but registry status is "
                f"'{live.status if live else 'MISSING'}' "
                f"(expected '{existing.status_after}'): divergence requires "
                "manual inspection, not silent replay"
            )
        raise ValueError(
            f"Candidate '{candidate_id}' already has COMPLETED decision "
            f"'{existing.decision}': conflicting '{verdict}' rejected, "
            "reversal is not supported in this wave"
        )

    if candidate.status != CandidateStatus.READY_FOR_REVIEW:
        raise ValueError(
            f"Candidate '{candidate_id}' is in state '{candidate.status}': "
            "human decisions require READY_FOR_REVIEW"
        )

    # ─── Wave 4C.3: baseline-bound promotion invariant (FAIL CLOSED) ─────────
    # Immediately before a human ACCEPT may cross the promotion boundary
    # (READY_FOR_REVIEW -> ACCEPTED), prove the candidate's recorded baseline
    # is still canonical and current. Reuses the 4C.1/4C.2 authority ONLY
    # (validate_candidate_baseline) — no second validator, pointer, registry
    # or hash algorithm. Ordering: BEFORE any durable write, so a blocked
    # approval never leaves a COMPLETED row (persisted truth never implies a
    # successful approval) and the candidate never crosses to ACCEPTED on
    # stale provenance. NO silent rebase, NO baseline/candidate mutation, NO
    # fabricated REJECT. REJECT/ARCHIVED decisions are NOT gated: rejecting
    # or deferring a stale candidate must remain possible.
    if verdict == "ACCEPT":
        from research_engine.v10.baselines.baseline_authority import (
            validate_candidate_baseline,
        )
        baseline_ok, baseline_reason = validate_candidate_baseline(
            candidate.baseline_id,
            candidate.change_definition.get("baseline_config_hash", ""),
        )
        if not baseline_ok:
            # Auditable non-effective row: HUMAN INTENT preserved
            # (decision/actor/reason), SYSTEM ELIGIBILITY denied. outcome
            # BASELINE_BLOCKED is never COMPLETED (get_decision() ignores
            # it), so the effective decision stays empty and a later retry
            # is permitted — same non-effective semantics as STATUS_FAILED.
            blocked = HumanDecision(
                candidate_id=candidate_id,
                decision=verdict,
                actor=actor.strip(),
                reason=reason.strip(),
                timestamp=timestamp_now(),
                evaluation_id=evaluation_id,
                status_before=candidate.status,
                status_after="",
                outcome="BASELINE_BLOCKED",
                error=f"baseline safety invariant: {baseline_reason}",
                recommendation_id=recommendation.recommendation_id,
                treatment_id=recommendation.treatment_id,
                baseline_id=recommendation.baseline_id,
                baseline_config_hash=recommendation.baseline_config_hash,
            )
            store._append_row_atomic(blocked)
            raise ValueError(
                f"Candidate '{candidate_id}' ACCEPT blocked by the baseline "
                f"safety invariant ({baseline_reason}): approval NOT applied, "
                "candidate remains READY_FOR_REVIEW"
            )

    # ── Wave 4E.2: recommendation provenance binding (FAIL CLOSED) ──────────
    # The durable governance evidence must let us prove:
    #   human decision -> exact recommendation -> exact evaluation
    #   -> exact candidate -> exact treatment -> exact baseline.
    # Every identity here is read from the recommendation (the 4E.1 durable
    # interpretation of the 4D.2 evaluation chain) or from the candidate's own
    # recorded evidence — NEVER reconstructed from mutable change_definition.
    if recommendation.candidate_id != candidate_id:
        _block_attempt(
            f"recommendation '{recommendation.recommendation_id}' "
            f"candidate_id mismatch: recommendation candidate="
            f"'{recommendation.candidate_id}' vs decision candidate="
            f"'{candidate_id}'",
            rec=recommendation,
        )
    # ACCEPT requires the candidate's own successful (IMPROVED) evidence.
    if verdict == "ACCEPT" and not evaluation_id:
        _block_attempt(
            f"Candidate '{candidate_id}' has no successful (IMPROVED) "
            "validation evidence: ACCEPT rejected",
            rec=recommendation,
        )
    # The recommendation's evaluation must be part of THIS candidate's
    # otherwise the recommendation must still be one of the candidate's own
    # evaluations (no silent substitution of another candidate's evaluation).
    if verdict == "ACCEPT":
        if recommendation.evaluation_id != evaluation_id:
            _block_attempt(
                f"recommendation '{recommendation.recommendation_id}' "
                f"evaluation_id mismatch: recommendation evaluation="
                f"'{recommendation.evaluation_id}' vs candidate evidence "
                f"evaluation='{evaluation_id}'",
                rec=recommendation,
            )
    elif known_eval_ids and recommendation.evaluation_id not in known_eval_ids:
        _block_attempt(
            f"recommendation '{recommendation.recommendation_id}' "
            f"evaluation_id '{recommendation.evaluation_id}' is not part of "
            f"candidate '{candidate_id}' evaluation evidence",
            rec=recommendation,
        )
    # Baseline identity must match the candidate's recorded baseline exactly.
    if recommendation.baseline_id != candidate.baseline_id:
        _block_attempt(
            f"recommendation '{recommendation.recommendation_id}' "
            f"baseline_id mismatch: recommendation baseline="
            f"'{recommendation.baseline_id}' vs candidate baseline="
            f"'{candidate.baseline_id}'",
            rec=recommendation,
        )
    # Baseline config provenance must match (Wave 4C contract).
    rec_config_hash = recommendation.baseline_config_hash
    cand_config_hash = candidate.change_definition.get(
        "baseline_config_hash", ""
    )
    if rec_config_hash != cand_config_hash:
        _block_attempt(
            f"recommendation '{recommendation.recommendation_id}' "
            f"baseline_config_hash mismatch: recommendation config_hash="
            f"'{rec_config_hash}' vs candidate config_hash="
            f"'{cand_config_hash}'",
            rec=recommendation,
        )
    # Treatment identity (4D.2 evidence chain). A missing treatment_id means
    # the measured effect cannot be attributed to a concrete treatment, so
    # promotion fails closed.
    if verdict == "ACCEPT" and not (recommendation.treatment_id or "").strip():
        _block_attempt(
            f"recommendation '{recommendation.recommendation_id}' has no "
            "treatment_id: cannot ACCEPT without evidence-attributed "
            "treatment identity",
            rec=recommendation,
        )
    # Mixed/malformed treatment evidence: one evaluation cannot have produced
    # two different treatment identities. If the canonical store holds
    # conflicting treatment identities for the same evaluation, the effect is
    # not attributable and ACCEPT fails closed. Only the durable store is
    # consulted — treatment identity is never recomputed from
    # change_definition.
    if verdict == "ACCEPT":
        same_eval = [
            r for r in rec_store.get_by_candidate_id(candidate_id)
            if r.evaluation_id == recommendation.evaluation_id
        ]
        distinct_treatments = {
            (r.treatment_id or "").strip()
            for r in same_eval
            if (r.treatment_id or "").strip()
        }
        if len(distinct_treatments) > 1:
            _block_attempt(
                f"recommendation '{recommendation.recommendation_id}' has "
                "mixed treatment evidence for evaluation "
                f"'{recommendation.evaluation_id}': conflicting treatment "
                f"identities {sorted(distinct_treatments)}: ACCEPT blocked",
                rec=recommendation,
            )
    # ACCEPT requires the recommendation to be ACTIONABLE (VALIDATED, not
    # promotion_blocked, complete provenance, sufficient sample, sufficient
    # confidence). Non-actionable research knowledge — INCONCLUSIVE,
    # insufficient evidence, promotion_blocked, small sample — is truthfully
    # preserved but can never be human-approved into ACCEPTED.
    if verdict == "ACCEPT" and not recommendation.actionable:
        _block_attempt(
            f"recommendation '{recommendation.recommendation_id}' is not "
            f"actionable: decision={recommendation.decision}, "
            f"limitations={recommendation.limitations}: ACCEPT blocked",
            rec=recommendation,
        )

    status_before = candidate.status
    status_after = _DECISION_TO_STATUS[verdict]
    record = HumanDecision(
        candidate_id=candidate_id,
        decision=verdict,
        actor=actor.strip(),
        reason=reason.strip(),
        timestamp=timestamp_now(),
        evaluation_id=evaluation_id,
        status_before=status_before,
        status_after=status_after,
        recommendation_id=recommendation.recommendation_id,
        treatment_id=recommendation.treatment_id,
        treatment_spec=recommendation.treatment_spec,
        baseline_id=recommendation.baseline_id,
        baseline_config_hash=recommendation.baseline_config_hash,
    )

    # Ordering: durable evidence FIRST (O_APPEND, never truncates), then the
    # registry transition. Reload the store view so memory matches disk even
    # under concurrent writers.
    store._append_row_atomic(record)
    store = CandidateDecisionStore(decisions_dir=store._dir.as_posix())

    try:
        registry.update_status(candidate_id, status_after)
    except Exception as e:
        store._rewrite_outcome(
            candidate_id, evaluation_id, outcome="STATUS_FAILED", error=str(e)[:200]
        )
        logger.warning(
            "[HUMAN_DECISION] status transition failed for %s: %s",
            candidate_id,
            str(e)[:150],
        )
        raise RuntimeError(
            f"Decision recorded but status transition {status_before} -> "
            f"{status_after} failed: {e}. Row marked STATUS_FAILED; "
            "retry after resolving the registry state."
        ) from e

    logger.info(
        "[HUMAN_DECISION] %s %s by %s: %s -> %s",
        candidate_id,
        verdict,
        record.actor,
        status_before,
        status_after,
    )
    return DecisionResult(ok=True, duplicate=False, decision=record)


def get_human_decision(
    candidate_id: str, *, decisions_dir: str | None = None
) -> HumanDecision | None:
    """Read back the effective human decision for a candidate (restart-safe)."""
    return CandidateDecisionStore(decisions_dir=decisions_dir).get_decision(
        candidate_id
    )


