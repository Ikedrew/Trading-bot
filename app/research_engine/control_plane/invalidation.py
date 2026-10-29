"""Explicit invalidation ledger for canonical research reports.

This module records known historical invalidation cases:
- R3: probability-of-ruin (used unrealistic inputs)
- R4: drawdown threshold (based on ALL-epoch EV=+0.675R)
- R5: position sizing (based on ALL-epoch data)
- Q19: old mixed-epoch positive-edge result

Invalidation targets specific evidence fingerprints, not filenames.
A future CURRENT rerun using the same canonical report filename can become valid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class InvalidationEntry:
    """A single invalidation record in the ledger."""
    canonical_question_id: str
    affected_report_path: str
    affected_evidence_fingerprint: dict[str, Any]
    reason: str
    invalidated_at: str
    replacement_report: str | None = None
    is_permanent: bool = False  # False = can be revalidated by future CURRENT rerun

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_question_id": self.canonical_question_id,
            "affected_report_path": self.affected_report_path,
            "affected_evidence_fingerprint": self.affected_evidence_fingerprint,
            "reason": self.reason,
            "invalidated_at": self.invalidated_at,
            "replacement_report": self.replacement_report,
            "is_permanent": self.is_permanent,
        }
# Historical invalidation records known from audit
_KNOWN_INVALIDATIONS = [
    InvalidationEntry(
        canonical_question_id="R3",
        affected_report_path="r3_probability_of_ruin.json",
        affected_evidence_fingerprint={
            "dataset_id": "shadow_trades_2026-07-27",
            "records_used": 100,
        },
        reason=(
            "R3 used unrealistic inputs (WR=80%, avg_win=2.0R, avg_loss=1.0R) "
            "that do not match CURRENT epoch evidence (WR=33%, EV=-0.20R). "
            "The report claimed P(ruin)=0% but CURRENT evidence shows ruin is effectively certain."
        ),
        invalidated_at="2026-09-13T00:00:00Z",
        replacement_report=None,
        is_permanent=False,
    ),
    InvalidationEntry(
        canonical_question_id="R4",
        affected_report_path="r4_drawdown_threshold.json",
        affected_evidence_fingerprint={
            "dataset_id": "shadow_trades_2026-07-27",
            "records_used": 901,
        },
        reason=(
            "R4 was based on ALL-epoch R-multiples (EV=+0.675R). "
            "With CURRENT EV=-0.20R, the system will monotonically approach "
            "and exceed any halt threshold."
        ),
        invalidated_at="2026-09-13T00:00:00Z",
        replacement_report=None,
        is_permanent=False,
    ),
    InvalidationEntry(
        canonical_question_id="R5",
        affected_report_path="r5_position_sizing.json",
        affected_evidence_fingerprint={
            "dataset_id": "shadow_trades_2026-07-27",
            "records_used": 901,
        },
        reason=(
            "R5 position sizing optimisation was based on ALL-epoch data (n=901, EV=+0.675R). "
            "Fixed 0.5% on CURRENT data = -57.2% return, 57.9% max drawdown."
        ),
        invalidated_at="2026-09-13T00:00:00Z",
        replacement_report=None,
        is_permanent=False,
    ),
    InvalidationEntry(
        canonical_question_id="E1",
        affected_report_path="q19_expected_value.json",
        affected_evidence_fingerprint={
            "epoch": "MIXED",
            "dataset_id": "shadow_trades_2026-07-27",
        },
        reason=(
            "Old Q19 report claimed POSITIVE_EDGE based on mixed-epoch data. "
            "The CURRENT canonical result shows NEGATIVE_EDGE (EV=-0.047R, n=1253)."
        ),
        invalidated_at="2026-09-13T00:00:00Z",
        replacement_report="q19_expected_value.json",
        is_permanent=False,
    ),
    InvalidationEntry(
        canonical_question_id="E1",
        affected_report_path="q19_expected_value.json",
        affected_evidence_fingerprint={
            "dataset_id": "shadow_trades_2026-09-06",
            "records_used": 1253,
            "epoch": "CURRENT",
        },
        reason=(
            "Q19's 1,253-row population bypassed canonical CURRENT filtering; "
            "build_fingerprint defaulted the unverified population to CURRENT."
        ),
        invalidated_at="2026-09-13T00:00:00Z",
        replacement_report=None,
        is_permanent=False,
    ),
]


def get_invalidations_for_question(question_id: str) -> list[InvalidationEntry]:
    qid = question_id.upper()
    return [inv for inv in _KNOWN_INVALIDATIONS if inv.canonical_question_id.upper() == qid]


def get_invalidations_for_dataset(dataset_id: str) -> list[InvalidationEntry]:
    return [
        inv for inv in _KNOWN_INVALIDATIONS
        if inv.affected_evidence_fingerprint.get("dataset_id") == dataset_id
    ]


def is_report_invalidated(
    report_path: str,
    evidence_fingerprint: dict[str, Any],
) -> InvalidationEntry | None:
    path_lower = report_path.lower()
    dataset_id = evidence_fingerprint.get("dataset_id", "")
    for inv in _KNOWN_INVALIDATIONS:
        if inv.affected_report_path.lower() == path_lower:
            fp = inv.affected_evidence_fingerprint
            if not dataset_id or fp.get("dataset_id") == dataset_id:
                match = True
                for key, value in fp.items():
                    if key != "dataset_id" and evidence_fingerprint.get(key) != value:
                        match = False
                        break
                if match:
                    return inv
    return None


def build_invalidations_dict() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for inv in _KNOWN_INVALIDATIONS:
        path = inv.affected_report_path.lower()
        dataset_id = inv.affected_evidence_fingerprint.get("dataset_id", "")
        if dataset_id:
            result.setdefault(path, set()).add(dataset_id)
    return result


def record_invalidations_json() -> list[dict[str, Any]]:
    return [inv.to_dict() for inv in _KNOWN_INVALIDATIONS]
