"""
Candidate Human Decision — Explicit governed ACCEPT / REJECT for Track-A.

Closes exactly ONE boundary:
    READY_FOR_REVIEW -> explicit human invocation -> durable decision
    -> CandidateRegistry.update_status() -> ACCEPTED or REJECTED

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
    outcome: str = "COMPLETED"  # COMPLETED | STATUS_FAILED
    error: str = ""

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


def record_human_decision(
    candidate_id: str,
    decision: str,
    *,
    actor: str = "",
    reason: str = "",
    registry_dir: str | None = None,
    decisions_dir: str | None = None,
) -> DecisionResult:
    """Record an explicit human ACCEPT/REJECT for a READY_FOR_REVIEW candidate.

    Preconditions (fail closed — nothing written, nothing changed):
        - decision in {ACCEPT, REJECT}
        - actor and reason non-empty
        - candidate exists and status == READY_FOR_REVIEW
        - ACCEPT requires existing IMPROVED ValidationEntry with validation_id;
          REJECT requires no positive evidence.
        - no conflicting COMPLETED decision already recorded.

    Idempotency: identical (candidate, evaluation, decision, actor, reason)
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

    if existing is not None:
        if (
            existing.decision == verdict
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
    if verdict == "ACCEPT" and not evaluation_id:
        raise ValueError(
            f"Candidate '{candidate_id}' has no successful (IMPROVED) "
            "validation evidence: ACCEPT rejected"
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


