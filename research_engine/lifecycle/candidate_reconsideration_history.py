"""Wave 6.3B — durable append-only reconsideration history."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ReconsiderationConflictError",
    "ReconsiderationBindingError",
    "CandidateReconsiderationRecord",
    "CandidateReconsiderationHistoryStore",
    "compute_successor_candidate_id",
]


class ReconsiderationConflictError(ValueError):
    """Same reconsideration identity with conflicting scientific content."""


class ReconsiderationBindingError(ValueError):
    """Persisted reconsideration truth cannot be proven exactly."""


def _non_empty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def compute_successor_candidate_id(
    *,
    predecessor_candidate_id: str,
    target_baseline_id: str,
    reconsideration_id: str,
) -> str:
    if not _non_empty_str(predecessor_candidate_id):
        raise ValueError("Missing predecessor candidate id")
    if not _non_empty_str(target_baseline_id):
        raise ValueError("Missing target baseline id")
    if not _non_empty_str(reconsideration_id):
        raise ValueError("Missing reconsideration id")
    safe_base = "".join(
        c if (c.isalnum() or c in ("-", "_")) else "-"
        for c in predecessor_candidate_id.strip()
    )[:48].strip("-") or "CAND"
    safe_tgt = "".join(
        c if (c.isalnum() or c in ("-", "_")) else "-"
        for c in target_baseline_id.strip()
    )[:24].strip("-") or "BASE"
    digest = _digest(
        {
            "predecessor_candidate_id": predecessor_candidate_id,
            "target_baseline_id": target_baseline_id,
            "reconsideration_id": reconsideration_id,
        }
    )[:12]
    return f"{safe_base}-R-{safe_tgt}-{digest}"


@dataclass(frozen=True)
class CandidateReconsiderationRecord:
    reconsideration_id: str
    historical_candidate_id: str
    historical_baseline_id: str
    historical_baseline_config_hash: str
    target_baseline_id: str
    target_baseline_config_hash: str
    impact_id: str
    continuity_id: str
    candidate_treatment_id: str
    application_id: str
    operation_id: str
    transition_candidate_id: str
    historical_candidate_status: str
    historical_evaluation_outcome: str
    historical_human_decision: str
    reconsideration_status: str
    reason_codes: tuple[str, ...]
    fresh_evidence_required: bool
    successor_candidate_id: str = ""
    successor_baseline_id: str = ""
    successor_baseline_config_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "reconsideration_id": self.reconsideration_id,
            "historical_candidate_id": self.historical_candidate_id,
            "historical_baseline_id": self.historical_baseline_id,
            "historical_baseline_config_hash": self.historical_baseline_config_hash,
            "target_baseline_id": self.target_baseline_id,
            "target_baseline_config_hash": self.target_baseline_config_hash,
            "impact_id": self.impact_id,
            "continuity_id": self.continuity_id,
            "candidate_treatment_id": self.candidate_treatment_id,
            "application_id": self.application_id,
            "operation_id": self.operation_id,
            "transition_candidate_id": self.transition_candidate_id,
            "historical_candidate_status": self.historical_candidate_status,
            "historical_evaluation_outcome": self.historical_evaluation_outcome,
            "historical_human_decision": self.historical_human_decision,
            "reconsideration_status": self.reconsideration_status,
            "reason_codes": list(self.reason_codes),
            "fresh_evidence_required": bool(self.fresh_evidence_required),
            "successor_candidate_id": self.successor_candidate_id,
            "successor_baseline_id": self.successor_baseline_id,
            "successor_baseline_config_hash": self.successor_baseline_config_hash,
        }

    def canonical_json(self) -> str:
        return _canonical(self.to_dict())

    def scientific_key(self) -> dict[str, Any]:
        d = self.to_dict()
        d.pop("successor_candidate_id", None)
        d.pop("successor_baseline_id", None)
        d.pop("successor_baseline_config_hash", None)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandidateReconsiderationRecord":
        shape = cls(
            reconsideration_id="x", historical_candidate_id="x",
            historical_baseline_id="x", historical_baseline_config_hash="x",
            target_baseline_id="x", target_baseline_config_hash="x",
            impact_id="x", continuity_id="x", candidate_treatment_id="x",
            application_id="x", operation_id="", transition_candidate_id="x",
            historical_candidate_status="x", historical_evaluation_outcome="x",
            historical_human_decision="x", reconsideration_status="NOT_ELIGIBLE",
            reason_codes=(), fresh_evidence_required=False,
        ).to_dict()
        if not isinstance(data, dict) or set(data) != set(shape):
            raise ValueError("Malformed reconsideration record")
        codes = data["reason_codes"]
        if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
            raise ValueError("Malformed reconsideration reason_codes")
        for f in (
            "reconsideration_id", "historical_candidate_id",
            "historical_baseline_id", "historical_baseline_config_hash",
            "target_baseline_id", "target_baseline_config_hash",
            "impact_id", "continuity_id", "candidate_treatment_id",
            "application_id", "transition_candidate_id",
            "historical_candidate_status", "historical_evaluation_outcome",
            "historical_human_decision", "reconsideration_status",
        ):
            if not _non_empty_str(data[f]):
                raise ValueError(f"Reconsideration record lacks {f}")
        if data["reconsideration_status"] not in (
            "ELIGIBLE_FOR_RECONSIDERATION", "NOT_ELIGIBLE",
            "FRESH_EVIDENCE_REQUIRED", "INDETERMINATE",
        ):
            raise ValueError("Unknown reconsideration status")
        return cls(
            reconsideration_id=data["reconsideration_id"],
            historical_candidate_id=data["historical_candidate_id"],
            historical_baseline_id=data["historical_baseline_id"],
            historical_baseline_config_hash=data[
                "historical_baseline_config_hash"],
            target_baseline_id=data["target_baseline_id"],
            target_baseline_config_hash=data["target_baseline_config_hash"],
            impact_id=data["impact_id"],
            continuity_id=data["continuity_id"],
            candidate_treatment_id=data["candidate_treatment_id"],
            application_id=data["application_id"],
            operation_id=data.get("operation_id", ""),
            transition_candidate_id=data["transition_candidate_id"],
            historical_candidate_status=data["historical_candidate_status"],
            historical_evaluation_outcome=data[
                "historical_evaluation_outcome"],
            historical_human_decision=data["historical_human_decision"],
            reconsideration_status=data["reconsideration_status"],
            reason_codes=tuple(codes),
            fresh_evidence_required=bool(data["fresh_evidence_required"]),
            successor_candidate_id=data.get("successor_candidate_id", ""),
            successor_baseline_id=data.get("successor_baseline_id", ""),
            successor_baseline_config_hash=data.get(
                "successor_baseline_config_hash", ""),
        )


_RECONSIDERATION_DIR = "data/research/lifecycle/reconsideration_history"
_RECONSIDERATION_FILE = "candidate_reconsideration_history.jsonl"


class CandidateReconsiderationHistoryStore:
    def __init__(self, reconsideration_dir: str | Path | None = None) -> None:
        self._dir = Path(reconsideration_dir or _RECONSIDERATION_DIR)
        self._records: list[CandidateReconsiderationRecord] = []
        self._load()

    @property
    def path(self) -> Path:
        return self._dir / _RECONSIDERATION_FILE

    def get(self, rid: str) -> CandidateReconsiderationRecord | None:
        for r in self._records:
            if r.reconsideration_id == rid:
                return r
        return None

    def list_all(self) -> list[CandidateReconsiderationRecord]:
        return list(self._records)

    def list_for_candidate(self, cid: str) -> list[CandidateReconsiderationRecord]:
        return sorted(
            [r for r in self._records if r.historical_candidate_id == cid],
            key=lambda r: (r.historical_baseline_id, r.target_baseline_id,
                           r.reconsideration_id),
        )

    def append(self, record: CandidateReconsiderationRecord) -> bool:
        if not isinstance(record, CandidateReconsiderationRecord):
            raise ValueError("Not a reconsideration record")
        if not _non_empty_str(record.reconsideration_id):
            raise ReconsiderationBindingError("Record lacks identity")
        existing = self.get(record.reconsideration_id)
        if existing is not None:
            if existing.scientific_key() != record.scientific_key():
                raise ReconsiderationConflictError(
                    f"Reconsideration {record.reconsideration_id!r} conflicts")
            if existing.successor_candidate_id != record.successor_candidate_id:
                raise ReconsiderationConflictError(
                    f"Conflicting successor for {record.reconsideration_id!r}")
            return False
        self._dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (record.canonical_json() + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        self._records.append(record)
        return True

    def bind_successor(self, rid: str, *,
                       successor_candidate_id: str,
                       successor_baseline_id: str,
                       successor_baseline_config_hash: str):
        if not _non_empty_str(successor_candidate_id):
            raise ReconsiderationBindingError("Missing successor id")
        existing = self.get(rid)
        if existing is None:
            raise ReconsiderationBindingError(f"Unknown {rid!r}")
        if existing.reconsideration_status != "ELIGIBLE_FOR_RECONSIDERATION":
            raise ReconsiderationBindingError(f"{rid!r} is blocked")
        if existing.successor_candidate_id:
            if existing.successor_candidate_id != successor_candidate_id:
                raise ReconsiderationConflictError(f"{rid!r} already bound")
            if (existing.successor_baseline_id != successor_baseline_id
                    or existing.successor_baseline_config_hash
                    != successor_baseline_config_hash):
                raise ReconsiderationConflictError("Conflicting N+1")
            return existing
        if (successor_baseline_id != existing.target_baseline_id
                or successor_baseline_config_hash
                != existing.target_baseline_config_hash):
            raise ReconsiderationBindingError("Must bind exact N+1")
        d = dict(existing.to_dict())
        d["successor_candidate_id"] = successor_candidate_id
        d["successor_baseline_id"] = successor_baseline_id
        d["successor_baseline_config_hash"] = successor_baseline_config_hash
        updated = CandidateReconsiderationRecord(
            reconsideration_id=d["reconsideration_id"],
            historical_candidate_id=d["historical_candidate_id"],
            historical_baseline_id=d["historical_baseline_id"],
            historical_baseline_config_hash=d["historical_baseline_config_hash"],
            target_baseline_id=d["target_baseline_id"],
            target_baseline_config_hash=d["target_baseline_config_hash"],
            impact_id=d["impact_id"], continuity_id=d["continuity_id"],
            candidate_treatment_id=d["candidate_treatment_id"],
            application_id=d["application_id"], operation_id=d["operation_id"],
            transition_candidate_id=d["transition_candidate_id"],
            historical_candidate_status=d["historical_candidate_status"],
            historical_evaluation_outcome=d["historical_evaluation_outcome"],
            historical_human_decision=d["historical_human_decision"],
            reconsideration_status=d["reconsideration_status"],
            reason_codes=tuple(d["reason_codes"]),
            fresh_evidence_required=bool(d["fresh_evidence_required"]),
            successor_candidate_id=successor_candidate_id,
            successor_baseline_id=successor_baseline_id,
            successor_baseline_config_hash=successor_baseline_config_hash,
        )
        if updated.scientific_key() != existing.scientific_key():
            raise ReconsiderationConflictError("Binding altered science")
        self._dir.mkdir(parents=True, exist_ok=True)
        rows = [r if r.reconsideration_id != rid else updated
                for r in self._records]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(r.canonical_json() + "\n" for r in rows),
                       encoding="utf-8")
        tmp.replace(self.path)
        self._records = rows
        return updated

    def _load(self) -> None:
        if not self.path.is_file():
            return
        by_id: dict[str, CandidateReconsiderationRecord] = {}
        order: list[str] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = CandidateReconsiderationRecord.from_dict(json.loads(line))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(
                    f"Corrupt reconsideration history in {self.path}: {exc}"
                ) from exc
            ex = by_id.get(rec.reconsideration_id)
            if ex is not None:
                if (ex.scientific_key() != rec.scientific_key()
                        or ex.successor_candidate_id
                        != rec.successor_candidate_id):
                    raise ReconsiderationConflictError(
                        f"Conflicting content for {rec.reconsideration_id!r}")
                continue
            by_id[rec.reconsideration_id] = rec
            order.append(rec.reconsideration_id)
        self._records = [by_id[k] for k in order]

