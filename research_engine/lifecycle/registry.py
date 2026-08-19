"""
Investigation Registry — Persistent store for all hypotheses and their lifecycle state.

Responsibilities:
    - CRUD operations on Hypothesis entities
    - Querying by status, category, age
    - Persistence to local JSON (append-only audit log + current state)
    - Lineage tracking between hypotheses (supersession, dependency)

Storage:
    logs/research_lifecycle/registry.json — current state of all hypotheses
    logs/research_lifecycle/audit_log.jsonl — append-only event log

This module NEVER modifies production V10.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.lifecycle.hypothesis import (
    ConclusionType,
    Hypothesis,
    HypothesisCategory,
    HypothesisStatus,
)

_REGISTRY_DIR = Path("logs/research_lifecycle")
_REGISTRY_FILE = _REGISTRY_DIR / "registry.json"
_AUDIT_LOG = _REGISTRY_DIR / "audit_log.jsonl"


class InvestigationRegistry:
    """
    Manages the collection of all research hypotheses.

    Provides:
    - Registration of new hypotheses
    - State queries (active, concluded, by category)
    - Persistence (load/save)
    - Audit logging (every mutation is logged)
    """

    def __init__(self):
        self._hypotheses: dict[str, Hypothesis] = {}
        self._load()

    # ─── CRUD ─────────────────────────────────────────────────────────

    def register(self, hypothesis: Hypothesis) -> str:
        """Register a new hypothesis. Returns hypothesis_id."""
        self._hypotheses[hypothesis.hypothesis_id] = hypothesis
        self._log_event("REGISTERED", hypothesis.hypothesis_id, hypothesis.title)
        self._save()
        return hypothesis.hypothesis_id

    def get(self, hypothesis_id: str) -> Hypothesis | None:
        """Get a hypothesis by ID."""
        return self._hypotheses.get(hypothesis_id)

    def update(self, hypothesis: Hypothesis) -> None:
        """Update an existing hypothesis (must already be registered)."""
        if hypothesis.hypothesis_id not in self._hypotheses:
            raise ValueError(f"Hypothesis {hypothesis.hypothesis_id} not registered")
        self._hypotheses[hypothesis.hypothesis_id] = hypothesis
        self._log_event("UPDATED", hypothesis.hypothesis_id, hypothesis.status.value)
        self._save()

    def all(self) -> list[Hypothesis]:
        """Return all hypotheses."""
        return list(self._hypotheses.values())

    # ─── QUERIES ──────────────────────────────────────────────────────

    def by_status(self, status: HypothesisStatus) -> list[Hypothesis]:
        """Get all hypotheses in a given status."""
        return [h for h in self._hypotheses.values() if h.status == status]

    def by_category(self, category: HypothesisCategory) -> list[Hypothesis]:
        """Get all hypotheses in a given category."""
        return [h for h in self._hypotheses.values() if h.category == category]

    def active(self) -> list[Hypothesis]:
        """Get hypotheses that are not yet concluded or promoted."""
        terminal = {HypothesisStatus.CONCLUDED, HypothesisStatus.PROMOTED}
        return [h for h in self._hypotheses.values() if h.status not in terminal]

    def concluded(self) -> list[Hypothesis]:
        """Get all concluded hypotheses."""
        return [h for h in self._hypotheses.values()
                if h.status in (HypothesisStatus.CONCLUDED, HypothesisStatus.PROMOTED)]

    def awaiting_approval(self) -> list[Hypothesis]:
        """Get hypotheses that are validated but awaiting human promotion approval."""
        return [h for h in self._hypotheses.values()
                if h.status == HypothesisStatus.CONCLUDED
                and h.conclusion_type == ConclusionType.VALIDATED
                and not h.human_approval_granted]

    def count_by_status(self) -> dict[str, int]:
        """Summary count by status."""
        counts: dict[str, int] = {}
        for h in self._hypotheses.values():
            counts[h.status.value] = counts.get(h.status.value, 0) + 1
        return counts

    # ─── PERSISTENCE ──────────────────────────────────────────────────

    def _save(self) -> None:
        """Persist current state to JSON."""
        _REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "version": "1.0",
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "hypotheses": {hid: h.to_dict() for hid, h in self._hypotheses.items()},
        }
        tmp = _REGISTRY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(_REGISTRY_FILE)

    def _load(self) -> None:
        """Load state from JSON if exists."""
        if not _REGISTRY_FILE.exists():
            return
        try:
            data = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
            for hid, h_data in data.get("hypotheses", {}).items():
                self._hypotheses[hid] = Hypothesis.from_dict(h_data)
        except (json.JSONDecodeError, KeyError, ValueError):
            pass  # Start fresh on corruption

    def _log_event(self, event_type: str, hypothesis_id: str, detail: str = "") -> None:
        """Append to audit log (never fails, never blocks)."""
        try:
            _REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event_type,
                "hypothesis_id": hypothesis_id,
                "detail": detail,
            }
            fd = os.open(str(_AUDIT_LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, (json.dumps(entry, separators=(",", ":")) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        except Exception:
            pass  # Audit logging must never block research
