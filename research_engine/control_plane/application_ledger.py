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
