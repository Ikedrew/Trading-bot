"""
Candidate Registry — Central registry for optimisation candidates.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus, ValidationEntry
from research_engine.v10.candidates.candidate_lifecycle import validate_transition, is_active

logger = logging.getLogger(__name__)

_STORAGE_DIR = "data/research/candidates"


class CandidateRegistry:
    """
    Central registry managing optimisation candidates.

    Persistence: data/research/candidates/candidates.jsonl
    """

    def __init__(self, storage_dir: str | None = None):
        self._dir = Path(storage_dir or _STORAGE_DIR)
        self._candidates: dict[str, CandidateRecord] = {}
        self._load()

    # ─── CRUD ─────────────────────────────────────────────────

    def create(self, candidate: CandidateRecord) -> None:
        """Register a new candidate."""
        if candidate.candidate_id in self._candidates:
            raise ValueError(f"Candidate '{candidate.candidate_id}' already exists")
        self._candidates[candidate.candidate_id] = candidate
        self._persist()
        logger.info(f"[CANDIDATE_REGISTRY] Created: {candidate.candidate_id}")

    def get(self, candidate_id: str) -> CandidateRecord | None:
        """Load a candidate by ID."""
        return self._candidates.get(candidate_id)

    def list_all(self) -> list[CandidateRecord]:
        """List all candidates."""
        return list(self._candidates.values())

    def list_by_status(self, status: str) -> list[CandidateRecord]:
        """List candidates with a specific status."""
        return [c for c in self._candidates.values() if c.status == status]

    def list_active(self) -> list[CandidateRecord]:
        """List candidates in active (non-terminal) states."""
        return [c for c in self._candidates.values() if is_active(c.status)]

    # ─── STATUS MANAGEMENT ────────────────────────────────────

    def update_status(self, candidate_id: str, new_status: str) -> None:
        """
        Update candidate status with lifecycle validation.

        Raises ValueError for invalid transitions.
        """
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            raise ValueError(f"Candidate '{candidate_id}' not found")

        validate_transition(candidate.status, new_status)
        candidate.status = new_status
        candidate.status_history.append({
            "status": new_status,
            "timestamp": timestamp_now(),
        })
        self._persist()
        logger.info(f"[CANDIDATE_REGISTRY] {candidate_id}: {candidate.status} -> {new_status}")

    # ─── VALIDATION HISTORY ───────────────────────────────────

    def add_validation_result(
        self,
        candidate_id: str,
        validation_id: str,
        decision: str,
        confidence: str = "",
        sample_size: int = 0,
        expectancy_delta: float = 0.0,
        regressions: list[str] | None = None,
    ) -> None:
        """Attach a validation result to a candidate."""
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            raise ValueError(f"Candidate '{candidate_id}' not found")

        entry = ValidationEntry(
            validation_id=validation_id,
            timestamp=timestamp_now(),
            decision=decision,
            confidence=confidence,
            sample_size=sample_size,
            expectancy_delta=expectancy_delta,
            regressions=regressions or [],
        )
        candidate.validation_history.append(entry)
        self._persist()

    # ─── ARCHIVE ──────────────────────────────────────────────

    def archive(self, candidate_id: str) -> None:
        """Archive a candidate (terminal state)."""
        self.update_status(candidate_id, CandidateStatus.ARCHIVED)

    # ─── PERSISTENCE ──────────────────────────────────────────

    def _persist(self) -> None:
        """Save all candidates to disk."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / "candidates.jsonl"
        lines = [json.dumps(c.to_dict(), default=str) for c in self._candidates.values()]
        path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")

    def _load(self) -> None:
        """Load candidates from disk."""
        path = self._dir / "candidates.jsonl"
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    data = json.loads(line)
                    record = CandidateRecord.from_dict(data)
                    self._candidates[record.candidate_id] = record
                except (json.JSONDecodeError, KeyError):
                    pass
