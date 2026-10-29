"""Decision ledger for human governance decisions."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DecisionState:
    NOT_REVIEWED = "NOT_REVIEWED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    REVOKED = "REVOKED"


_VALID_DECISIONS = frozenset({
    DecisionState.NOT_REVIEWED, DecisionState.UNDER_REVIEW,
    DecisionState.APPROVED, DecisionState.REJECTED,
    DecisionState.DEFERRED, DecisionState.REVOKED,
})

_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    DecisionState.NOT_REVIEWED: frozenset({
        DecisionState.UNDER_REVIEW, DecisionState.APPROVED, DecisionState.REJECTED, DecisionState.DEFERRED
    }),
    DecisionState.UNDER_REVIEW: frozenset({
        DecisionState.APPROVED, DecisionState.REJECTED, DecisionState.DEFERRED, DecisionState.UNDER_REVIEW
    }),
    DecisionState.APPROVED: frozenset({DecisionState.REVOKED, DecisionState.APPROVED}),
    DecisionState.REJECTED: frozenset({DecisionState.UNDER_REVIEW, DecisionState.REJECTED}),
    DecisionState.DEFERRED: frozenset({DecisionState.UNDER_REVIEW, DecisionState.APPROVED, DecisionState.REJECTED}),
    DecisionState.REVOKED: frozenset({DecisionState.UNDER_REVIEW, DecisionState.REVOKED}),
}


@dataclass
class DecisionRecord:
    decision_id: str
    candidate_id: str
    canonical_question_id: str
    decision: str
    decided_at: str
    decided_by: str = ""
    reason: str = ""
    evidence_reference: str = ""
    previous_decision_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "candidate_id": self.candidate_id,
            "canonical_question_id": self.canonical_question_id,
            "decision": self.decision,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "reason": self.reason,
            "evidence_reference": self.evidence_reference,
            "previous_decision_id": self.previous_decision_id,
        }


class DecisionLedger:
    """Append-only governance decision ledger."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self._path = Path(storage_path) if storage_path is not None else _DEFAULT_PATH

    def append(
        self,
        candidate_id: str,
        canonical_question_id: str,
        decision: str,
        *,
        decided_by: str = "",
        reason: str = "",
        evidence_reference: str = "",
        previous_decision_id: str = "",
        decision_id: str | None = None,
        timestamp: str | None = None,
    ) -> DecisionRecord:
        """Record a new governance decision. Fails closed on invalid transitions."""
        if decision not in _VALID_DECISIONS:
            raise ValueError(f"Invalid decision state: {decision!r}")

        existing = self.get_latest_for_candidate(candidate_id)
        if existing and existing.decision in _VALID_TRANSITIONS:
            allowed = _VALID_TRANSITIONS[existing.decision]
            if decision not in allowed:
                raise ValueError(
                    f"Invalid transition from {existing.decision!r} to {decision!r}. "
                    f"Allowed: {sorted(allowed)}"
                )

        record = DecisionRecord(
            decision_id=decision_id or f"DEC-{candidate_id}-{int(datetime.now(timezone.utc).timestamp())}",
            candidate_id=candidate_id,
            canonical_question_id=canonical_question_id,
            decision=decision,
            decided_at=timestamp or datetime.now(timezone.utc).isoformat(),
            decided_by=decided_by,
            reason=reason,
            evidence_reference=evidence_reference,
            previous_decision_id=previous_decision_id or (existing.decision_id if existing else ""),
        )

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict(), default=str) + "\n")
        return record

    def list_all(self) -> list[DecisionRecord]:
        if not self._path.is_file():
            return []
        records: list[DecisionRecord] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                records.append(DecisionRecord(**data))
            except (json.JSONDecodeError, TypeError):
                continue
        return records

    def get_latest_for_candidate(self, candidate_id: str) -> DecisionRecord | None:
        matching = [r for r in self.list_all() if r.candidate_id == candidate_id]
        if not matching:
            return None
        return max(matching, key=lambda r: r.decided_at)

    def get_for_question(self, canonical_question_id: str) -> list[DecisionRecord]:
        qid = canonical_question_id.upper()
        return [r for r in self.list_all() if r.canonical_question_id.upper() == qid]


def validate_decision_transition(current: str, new: str) -> tuple[bool, str]:
    if new not in _VALID_DECISIONS:
        return False, f"Invalid decision state: {new!r}"
    if current in _VALID_TRANSITIONS:
        if new not in _VALID_TRANSITIONS[current]:
            return False, f"Cannot transition from {current!r} to {new!r}"
    return True, ""


_DEFAULT_PATH = Path("data/research/governance/decisions.jsonl")
