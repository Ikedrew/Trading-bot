"""Production application ledger."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ApplicationState:
    NOT_APPLIED = "NOT_APPLIED"
    APPROVED_NOT_DEPLOYED = "APPROVED_NOT_DEPLOYED"
    DEPLOYED = "DEPLOYED"
    VERIFIED = "VERIFIED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


_VALID_APPLICATION_STATES = frozenset({
    ApplicationState.NOT_APPLIED, ApplicationState.APPROVED_NOT_DEPLOYED,
    ApplicationState.DEPLOYED, ApplicationState.VERIFIED,
    ApplicationState.ROLLED_BACK, ApplicationState.FAILED, ApplicationState.UNKNOWN,
})

_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    ApplicationState.NOT_APPLIED: frozenset({
        ApplicationState.APPROVED_NOT_DEPLOYED, ApplicationState.NOT_APPLIED, ApplicationState.UNKNOWN
    }),
    ApplicationState.APPROVED_NOT_DEPLOYED: frozenset({
        ApplicationState.DEPLOYED, ApplicationState.APPROVED_NOT_DEPLOYED, ApplicationState.NOT_APPLIED
    }),
    ApplicationState.DEPLOYED: frozenset({
        ApplicationState.VERIFIED, ApplicationState.ROLLED_BACK, ApplicationState.FAILED, ApplicationState.DEPLOYED
    }),
    ApplicationState.VERIFIED: frozenset({ApplicationState.ROLLED_BACK, ApplicationState.VERIFIED}),
    ApplicationState.ROLLED_BACK: frozenset({
        ApplicationState.DEPLOYED, ApplicationState.NOT_APPLIED, ApplicationState.ROLLED_BACK
    }),
    ApplicationState.FAILED: frozenset({
        ApplicationState.DEPLOYED, ApplicationState.NOT_APPLIED, ApplicationState.FAILED
    }),
    ApplicationState.UNKNOWN: frozenset({
        ApplicationState.NOT_APPLIED, ApplicationState.DEPLOYED, ApplicationState.UNKNOWN
    }),
}


@dataclass
class ApplicationRecord:
    application_id: str
    candidate_id: str
    canonical_question_id: str
    state: str
    occurred_at: str
    actor: str = ""
    target_config_key: str = ""
    previous_value: str = ""
    new_value: str = ""
    deployment_reference: str = ""
    verification_evidence: str = ""
    reason: str = ""
    # Wave 4E.3: exact persisted approval provenance; empty for legacy rows.
    recommendation_id: str = ""
    evaluation_id: str = ""
    treatment_id: str = ""
    baseline_id: str = ""
    baseline_config_hash: str = ""
    human_decision_outcome: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "candidate_id": self.candidate_id,
            "canonical_question_id": self.canonical_question_id,
            "state": self.state,
            "occurred_at": self.occurred_at,
            "actor": self.actor,
            "target_config_key": self.target_config_key,
            "previous_value": self.previous_value,
            "new_value": self.new_value,
            "deployment_reference": self.deployment_reference,
            "verification_evidence": self.verification_evidence,
            "reason": self.reason,
            "recommendation_id": self.recommendation_id,
            "evaluation_id": self.evaluation_id,
            "treatment_id": self.treatment_id,
            "baseline_id": self.baseline_id,
            "baseline_config_hash": self.baseline_config_hash,
            "human_decision_outcome": self.human_decision_outcome,
        }


class ApplicationLedger:
    """Append-only production application ledger."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self._path = Path(storage_path) if storage_path is not None else _DEFAULT_PATH

    def append(
        self,
        candidate_id: str,
        canonical_question_id: str,
        state: str,
        *,
        actor: str = "",
        target_config_key: str = "",
        previous_value: str = "",
        new_value: str = "",
        deployment_reference: str = "",
        verification_evidence: str = "",
        reason: str = "",
        application_id: str | None = None,
        timestamp: str | None = None,
        recommendation_id: str = "",
        evaluation_id: str = "",
        treatment_id: str = "",
        baseline_id: str = "",
        baseline_config_hash: str = "",
        human_decision_outcome: str = "",
    ) -> ApplicationRecord:
        if state not in _VALID_APPLICATION_STATES:
            raise ValueError(f"Invalid application state: {state!r}")

        existing = self.get_latest_for_candidate(candidate_id)
        if existing and existing.state in _VALID_TRANSITIONS:
            allowed = _VALID_TRANSITIONS[existing.state]
            if state not in allowed:
                raise ValueError(
                    f"Invalid transition from {existing.state!r} to {state!r}. "
                    f"Allowed: {sorted(allowed)}"
                )

        record = ApplicationRecord(
            application_id=application_id or f"APP-{candidate_id}-{int(datetime.now(timezone.utc).timestamp())}",
            candidate_id=candidate_id,
            canonical_question_id=canonical_question_id,
            state=state,
            occurred_at=timestamp or datetime.now(timezone.utc).isoformat(),
            actor=actor,
            target_config_key=target_config_key,
            previous_value=previous_value,
            new_value=new_value,
            deployment_reference=deployment_reference,
            verification_evidence=verification_evidence,
            reason=reason,
            recommendation_id=recommendation_id,
            evaluation_id=evaluation_id,
            treatment_id=treatment_id,
            baseline_id=baseline_id,
            baseline_config_hash=baseline_config_hash,
            human_decision_outcome=human_decision_outcome,
        )

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict(), default=str) + "\n")
        return record

    def list_all(self) -> list[ApplicationRecord]:
        if not self._path.is_file():
            return []
        records: list[ApplicationRecord] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                records.append(ApplicationRecord(**data))
            except (json.JSONDecodeError, TypeError):
                continue
        return records

    # ─── Wave 4E.3: governed approval → application eligibility ──────────────
    _REQUIRED_IDENTITIES = (
        "recommendation_id", "evaluation_id", "candidate_id", "treatment_id",
        "baseline_id", "baseline_config_hash",
    )

    def create_application_from_approval(
        self,
        candidate_id: str,
        recommendation_id: str,
        *,
        decisions_dir: str | None = None,
        recommendations_dir: str | None = None,
        registry_dir: str | None = None,
    ) -> ApplicationRecord:
        """Create the APPROVED_NOT_DEPLOYED application for ONE exact approval.

        Wave 4E.3: application creation is permitted ONLY from the exact
        effective canonical human ACCEPT persisted by the Wave 4E.2
        CandidateDecisionStore, bound to the exact canonical
        CandidateRecommendation (Wave 4E.1). Caller-supplied HumanDecision
        objects are never consulted: governance truth is re-resolved from the
        durable decision store here, so a fabricated in-memory
        HumanDecision(decision="ACCEPT", outcome="COMPLETED", ...) cannot
        cross this boundary.
        """
        cid = (candidate_id or "").strip()
        rid = (recommendation_id or "").strip()
        if not cid or not rid:
            raise ValueError(
                "create_application_from_approval requires a non-empty "
                "candidate_id and recommendation_id"
            )

        # ─── Canonical persisted decision (never a caller-supplied object) ──
        from research_engine.v10.candidates.candidate_decision import (
            CandidateDecisionStore,
        )
        decision = CandidateDecisionStore(
            decisions_dir=decisions_dir
        ).get_decision(cid)
        if decision is None:
            raise ValueError(
                f"No effective persisted human decision for candidate "
                f"'{cid}': application requires the canonical Wave 4E.2 "
                "COMPLETED ACCEPT; nothing written"
            )
        if decision.decision != "ACCEPT":
            raise ValueError(
                f"Persisted effective decision for candidate '{cid}' is "
                f"'{decision.decision}', not ACCEPT: application blocked"
            )
        if decision.outcome != "COMPLETED":
            raise ValueError(
                f"Persisted effective decision for candidate '{cid}' has "
                f"outcome '{decision.outcome}': application blocked"
            )
        # Without an exact recommendation binding the persisted decision
        # carries no attributable evidence chain (pre-4E.2 governance row).
        if decision.recommendation_id != rid:
            raise ValueError(
                f"Persisted effective decision for candidate '{cid}' binds "
                f"recommendation '{decision.recommendation_id}', not the "
                f"requested '{rid}': application blocked"
            )

        from research_engine.lifecycle.candidate_recommendation import RecommendationStore

        recommendation = RecommendationStore(
            recommendations_dir=recommendations_dir
        ).get_by_recommendation_id(rid)
        if recommendation is None:
            raise ValueError(f"Recommendation '{rid}' not found: application blocked")
        for name in self._REQUIRED_IDENTITIES:
            approved = getattr(decision, name)
            recorded = getattr(recommendation, name)
            if not all(isinstance(value, str) and value.strip() for value in (approved, recorded)):
                raise ValueError(f"Missing required identity '{name}': application blocked")
            if approved != recorded:
                raise ValueError(f"Decision/recommendation '{name}' mismatch: application blocked")

        # Revalidate canonical evidence even on replay. Never reuse a different
        # recommendation, nor bless a conflicting low-level ledger row.
        for existing in self.list_all():
            if existing.candidate_id == cid and existing.recommendation_id == rid:
                if (
                    any(getattr(existing, name) != getattr(decision, name)
                        for name in self._REQUIRED_IDENTITIES)
                    or existing.human_decision_outcome != decision.outcome
                    or existing.actor != decision.actor
                    or existing.reason != decision.reason
                ):
                    raise ValueError("Existing application conflicts with persisted approval")
                return existing

        # Read candidate ONLY for question attribution, not governance identity.
        # Unknown/ambiguous question provenance stays unmapped, never guessed.
        from research_engine.v10.candidates.candidate_registry import CandidateRegistry
        from research_engine.control_plane.state_builder import _resolve_question

        candidate = CandidateRegistry(storage_dir=registry_dir).get(cid)
        canonical_question_id = ""
        if candidate is not None and candidate.created_from_question:
            try:
                canonical_question_id = _resolve_question(candidate.created_from_question).id
            except KeyError:
                pass

        # Existing append primitive owns persistence and its transition guard.
        # No scoring, baseline activation, candidate mutation or deployment.
        return self.append(
            decision.candidate_id,
            canonical_question_id,
            ApplicationState.APPROVED_NOT_DEPLOYED,
            application_id=f"APP-{cid}-{rid}",
            actor=decision.actor,
            reason=decision.reason,
            recommendation_id=decision.recommendation_id,
            evaluation_id=decision.evaluation_id,
            treatment_id=decision.treatment_id,
            baseline_id=decision.baseline_id,
            baseline_config_hash=decision.baseline_config_hash,
            human_decision_outcome=decision.outcome,
        )

    def get_latest_for_candidate(self, candidate_id: str) -> ApplicationRecord | None:
        matching = [r for r in self.list_all() if r.candidate_id == candidate_id]
        if not matching:
            return None
        return max(matching, key=lambda r: r.occurred_at)

    def get_for_question(self, canonical_question_id: str) -> list[ApplicationRecord]:
        qid = canonical_question_id.upper()
        return [r for r in self.list_all() if r.canonical_question_id.upper() == qid]


def validate_application_transition(current: str, new: str) -> tuple[bool, str]:
    if new not in _VALID_APPLICATION_STATES:
        return False, f"Invalid application state: {new!r}"
    if current in _VALID_TRANSITIONS:
        if new not in _VALID_TRANSITIONS[current]:
            return False, f"Cannot transition from {current!r} to {new!r}"
    return True, ""


_DEFAULT_PATH = Path("data/research/governance/application.jsonl")
